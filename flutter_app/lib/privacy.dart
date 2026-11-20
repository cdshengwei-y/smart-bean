import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

/// Increment when the data flow or disclosure text changes materially.
const privacyConsentVersion = '2026-09-14-v1';

/// Provide the real public URL at build time once it is published.
/// Example: --dart-define=PRIVACY_POLICY_URL=https://example.com/privacy
const privacyPolicyUrl = String.fromEnvironment('PRIVACY_POLICY_URL');

const privacyDisclosure = '''
智能录音豆的蓝牙配对、开始/停止录音和本地设备管理可以离线使用。

当你选择上传、转写或 AI 处理时：
• WAV 音频会发送到你配置的配套服务；
• 配套服务会把音频发送给当前选择的语音转写服务（例如阿里云 Qwen ASR，或你配置的自定义 ASR），仅用于生成转写文本；
• 开启文字优化或工作内容筛选时，转写文本和筛选设置会发送给配套服务配置的文本 AI 服务；
• 不会为了这些处理直接发送你的邮箱、联系人或其他无关账户资料；
• Obsidian 同步只会在你主动配置并启动后，将结果写入你指定的 Vault。

你可以拒绝授权。拒绝后仍可使用蓝牙连接和本地录音，但上传、转写和 AI 处理不会执行。具体服务主体、保存期限和删除方式以公开隐私政策为准。
''';

Future<bool> hasPrivacyConsent() async {
  final preferences = await SharedPreferences.getInstance();
  return preferences.getString('privacy_consent_version') ==
      privacyConsentVersion;
}

Future<void> revokePrivacyConsent() async {
  final preferences = await SharedPreferences.getInstance();
  await preferences.remove('privacy_consent_version');
}

Future<bool> ensurePrivacyConsent(BuildContext context) async {
  if (await hasPrivacyConsent()) return true;
  if (!context.mounted) return false;
  final accepted = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (context) => AlertDialog(
      title: const Text('上传与 AI 数据使用说明'),
      content: const SingleChildScrollView(child: Text(privacyDisclosure)),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('暂不授权'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('同意并继续'),
        ),
      ],
    ),
  );
  if (accepted != true) return false;
  final preferences = await SharedPreferences.getInstance();
  await preferences.setString('privacy_consent_version', privacyConsentVersion);
  return true;
}

class PrivacyPage extends StatelessWidget {
  const PrivacyPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('隐私与数据')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
        children: [
          const Text(privacyDisclosure),
          const SizedBox(height: 20),
          const Text('隐私政策', style: TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(height: 6),
          if (privacyPolicyUrl.isEmpty)
            const Text('公开隐私政策网址尚未配置。发布前请通过 PRIVACY_POLICY_URL 提供 HTTPS 地址。')
          else
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.open_in_new),
              title: const Text('查看公开隐私政策'),
              subtitle: Text(privacyPolicyUrl),
              onTap: () => launchUrl(Uri.parse(privacyPolicyUrl)),
            ),
          const SizedBox(height: 18),
          OutlinedButton.icon(
            onPressed: () async {
              await revokePrivacyConsent();
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('授权已撤回，下次上传或 AI 处理前会再次询问')),
                );
              }
            },
            icon: const Icon(Icons.lock_reset_outlined),
            label: const Text('撤回上传与 AI 处理授权'),
          ),
        ],
      ),
    );
  }
}
