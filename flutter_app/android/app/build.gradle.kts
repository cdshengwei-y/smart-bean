plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

import java.util.Properties

val releaseSigningProperties = Properties()
val releaseSigningPropertiesFile = file(System.getProperty("user.home") + "/.android/recording-bean-release.properties")
if (releaseSigningPropertiesFile.isFile) {
    releaseSigningPropertiesFile.inputStream().use { releaseSigningProperties.load(it) }
}

android {
    namespace = "com.aibean.ai_recording_bean"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.aibean.controller"
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            val releaseKeystore = file(
                releaseSigningProperties.getProperty(
                    "storeFile",
                    System.getProperty("user.home") + "/.android/recording-bean-release.jks"
                )
            )
            if (releaseKeystore.isFile &&
                releaseSigningProperties.getProperty("storePassword").isNullOrBlank().not() &&
                releaseSigningProperties.getProperty("keyPassword").isNullOrBlank().not()
            ) {
                signingConfig = signingConfigs.create("recordingBeanRelease") {
                    storeFile = releaseKeystore
                    storePassword = releaseSigningProperties.getProperty("storePassword")
                    keyAlias = releaseSigningProperties.getProperty("keyAlias", "recording-bean")
                    keyPassword = releaseSigningProperties.getProperty("keyPassword")
                }
            } else {
                throw GradleException(
                    "Missing Android release signing configuration. " +
                        "Create ~/.android/recording-bean-release.properties with storePassword and keyPassword."
                )
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
