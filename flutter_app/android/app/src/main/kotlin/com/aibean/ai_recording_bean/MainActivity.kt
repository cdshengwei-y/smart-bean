package com.aibean.ai_recording_bean

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    private val fileExecutor = Executors.newSingleThreadExecutor()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.aibean.controller/downloads",
        ).setMethodCallHandler { call, result ->
            if (call.method != "saveAudio") {
                result.notImplemented()
                return@setMethodCallHandler
            }
            val sourcePath = call.argument<String>("sourcePath")
            val fileName = call.argument<String>("fileName")
            if (sourcePath.isNullOrBlank() || fileName.isNullOrBlank()) {
                result.error("invalid_arguments", "下载文件参数不完整", null)
                return@setMethodCallHandler
            }
            fileExecutor.execute {
                try {
                    saveAudio(File(sourcePath), File(fileName).name)
                    runOnUiThread { result.success(null) }
                } catch (error: SecurityException) {
                    runOnUiThread {
                        result.error(
                            "storage_permission_required",
                            "需要存储权限才能保存录音",
                            null,
                        )
                    }
                } catch (error: Exception) {
                    runOnUiThread {
                        result.error("save_failed", error.message ?: "保存录音失败", null)
                    }
                }
            }
        }
    }

    private fun saveAudio(source: File, fileName: String) {
        require(source.isFile) { "下载的临时文件不存在" }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            saveWithMediaStore(source, fileName)
        } else {
            saveToLegacyDownloads(source, fileName)
        }
    }

    private fun saveWithMediaStore(source: File, fileName: String) {
        val resolver = contentResolver
        val collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val relativePath = "${Environment.DIRECTORY_DOWNLOADS}/智能录音豆/"
        var targetUri = resolver.query(
            collection,
            arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME} = ? AND ${MediaStore.Downloads.RELATIVE_PATH} = ?",
            arrayOf(fileName, relativePath),
            null,
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                ContentUris.withAppendedId(collection, cursor.getLong(0))
            } else {
                null
            }
        }

        if (targetUri == null) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, fileName)
                put(MediaStore.Downloads.MIME_TYPE, "audio/wav")
                put(MediaStore.Downloads.RELATIVE_PATH, relativePath)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            targetUri = requireNotNull(resolver.insert(collection, values)) {
                "无法在下载目录创建录音文件"
            }
        }

        val output = requireNotNull(resolver.openOutputStream(targetUri, "wt")) {
            "无法写入下载目录"
        }
        output.use { destination -> source.inputStream().use { it.copyTo(destination) } }
        resolver.update(
            targetUri,
            ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) },
            null,
            null,
        )
    }

    @Suppress("DEPRECATION")
    private fun saveToLegacyDownloads(source: File, fileName: String) {
        if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("需要存储权限")
        }
        val folder = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "智能录音豆",
        )
        check(folder.exists() || folder.mkdirs()) { "无法创建下载目录" }
        val target = File(folder, fileName)
        source.copyTo(target, overwrite = true)
        MediaScannerConnection.scanFile(
            this,
            arrayOf(target.absolutePath),
            arrayOf("audio/wav"),
            null,
        )
    }

    override fun onDestroy() {
        fileExecutor.shutdown()
        super.onDestroy()
    }
}
