import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'appearance_settings.dart';
import 'controller.dart';
import 'models.dart';
import 'privacy.dart';
import 'services/auth_api.dart';
import 'services/ble_recorder.dart';

const canvas = Color(0xFFF5F8F4);
const danger = Color(0xFFB33F3A);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final preferences = await SharedPreferences.getInstance();
  runApp(
    RecordingBeanApp(
      initialDarkMode: preferences.getBool('dark_mode') ?? false,
      initialAccent: AppearanceAccent.fromStorage(
        preferences.getString('appearance_accent'),
      ),
    ),
  );
}

class RecordingBeanApp extends StatefulWidget {
  const RecordingBeanApp({
    super.key,
    required this.initialDarkMode,
    required this.initialAccent,
  });

  final bool initialDarkMode;
  final AppearanceAccent initialAccent;

  @override
  State<RecordingBeanApp> createState() => _RecordingBeanAppState();
}

class _RecordingBeanAppState extends State<RecordingBeanApp> {
  late bool darkMode;
  late AppearanceAccent accent;

  @override
  void initState() {
    super.initState();
    darkMode = widget.initialDarkMode;
    accent = widget.initialAccent;
  }

  Future<void> setDarkMode(bool value) async {
    if (darkMode == value) return;
    setState(() => darkMode = value);
    final preferences = await SharedPreferences.getInstance();
    await preferences.setBool('dark_mode', darkMode);
  }

  Future<void> setAccent(AppearanceAccent value) async {
    if (accent == value) return;
    setState(() => accent = value);
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString('appearance_accent', accent.storageValue);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '智能录音豆',
      debugShowCheckedModeBanner: false,
      theme: _buildTheme(Brightness.light, accent),
      darkTheme: _buildTheme(Brightness.dark, accent),
      themeMode: darkMode ? ThemeMode.dark : ThemeMode.light,
      home: _AuthGate(
        darkMode: darkMode,
        accent: accent,
        setDarkMode: setDarkMode,
        setAccent: setAccent,
      ),
    );
  }
}

ThemeData _buildTheme(Brightness brightness, AppearanceAccent accent) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: accent.color,
    brightness: brightness,
    surface: dark ? const Color(0xFF171D1A) : Colors.white,
    error: dark ? const Color(0xFFFFB4AB) : danger,
  );
  return ThemeData(
    colorScheme: scheme,
    scaffoldBackgroundColor: dark ? const Color(0xFF101512) : canvas,
    useMaterial3: true,
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.surface,
      foregroundColor: scheme.onSurface,
      surfaceTintColor: Colors.transparent,
    ),
    cardTheme: CardThemeData(
      color: dark ? const Color(0xFF1B231F) : Colors.white,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(8)),
      ),
    ),
    inputDecorationTheme: const InputDecorationTheme(
      border: OutlineInputBorder(
        borderRadius: BorderRadius.all(Radius.circular(8)),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
  );
}

class _AuthGate extends StatefulWidget {
  const _AuthGate({
    required this.darkMode,
    required this.accent,
    required this.setDarkMode,
    required this.setAccent,
  });

  final bool darkMode;
  final AppearanceAccent accent;
  final Future<void> Function(bool value) setDarkMode;
  final Future<void> Function(AppearanceAccent value) setAccent;

  @override
  State<_AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<_AuthGate> {
  final authApi = AuthApi();
  AuthSession? session;
  Uri? backendAddress;
  String restoreMessage = '';
  bool loading = true;

  @override
  void initState() {
    super.initState();
    unawaited(_restoreSession());
  }

  @override
  void dispose() {
    authApi.close();
    super.dispose();
  }

  Future<void> _restoreSession() async {
    final preferences = await SharedPreferences.getInstance();
    try {
      // Account login is optional. Do not treat the LAN status-service default
      // as an account endpoint, otherwise a reviewer without that LAN service
      // sees a login failure before reaching the local recording workflow.
      final storedBackendAddress = preferences
          .getString('backend_status_address')
          ?.trim();
      if (storedBackendAddress != null && storedBackendAddress.isNotEmpty) {
        backendAddress = authApi.normalizeAddress(storedBackendAddress);
      }
      final token = preferences.getString('auth_token');
      final cachedEmail = preferences.getString('auth_email')?.trim() ?? '';
      if (token != null && token.isNotEmpty && cachedEmail.isNotEmpty) {
        session = AuthSession(
          token: token,
          user: AuthUser(
            id: preferences.getInt('auth_user_id') ?? 0,
            email: cachedEmail,
            createdAt: preferences.getString('auth_created_at') ?? '',
          ),
        );
      }
    } catch (error) {
      restoreMessage = _authErrorText(error);
    } finally {
      if (mounted) setState(() => loading = false);
    }

    final restoredToken = session?.token;
    final restoredAddress = backendAddress;
    if (restoredToken != null && restoredAddress != null) {
      unawaited(_validateSessionInBackground(restoredAddress, restoredToken));
    }
  }

  Future<void> _validateSessionInBackground(Uri address, String token) async {
    try {
      final user = await authApi.me(address, token);
      final preferences = await SharedPreferences.getInstance();
      await _cacheUser(preferences, user);
      if (!mounted || session?.token != token) return;
      setState(() => session = AuthSession(token: token, user: user));
    } catch (error) {
      // A network failure must not lock the user out of the locally cached app.
      if (error is! AuthException || error.statusCode != 401) return;
      final preferences = await SharedPreferences.getInstance();
      await _clearCachedSession(preferences);
      if (!mounted || session?.token != token) return;
      setState(() {
        session = null;
        restoreMessage = '登录已过期，请联网后重新登录。';
      });
    }
  }

  Future<void> _cacheUser(SharedPreferences preferences, AuthUser user) async {
    await preferences.setString('auth_email', user.email);
    await preferences.setInt('auth_user_id', user.id);
    await preferences.setString('auth_created_at', user.createdAt);
  }

  Future<void> _clearCachedSession(SharedPreferences preferences) async {
    await preferences.remove('auth_token');
    await preferences.remove('auth_email');
    await preferences.remove('auth_user_id');
    await preferences.remove('auth_created_at');
  }

  Future<void> _handleAuthenticated(AuthSession value) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString('auth_token', value.token);
    await _cacheUser(preferences, value.user);
    if (!mounted) return;
    setState(() {
      session = value;
      restoreMessage = '';
    });
  }

  Future<void> _logout() async {
    final current = session;
    if (current != null && backendAddress != null) {
      try {
        await authApi.logout(backendAddress!, current.token);
      } catch (_) {}
    }
    final preferences = await SharedPreferences.getInstance();
    await _clearCachedSession(preferences);
    if (!mounted) return;
    setState(() {
      session = null;
      restoreMessage = '';
    });
  }

  Future<void> _changePassword(
    String currentPassword,
    String newPassword,
  ) async {
    final current = session;
    final address = backendAddress;
    if (current == null || address == null) {
      throw const AuthException('登录已失效，请重新登录。');
    }
    await authApi.changePassword(
      address,
      current.token,
      currentPassword,
      newPassword,
    );
  }

  @override
  Widget build(BuildContext context) {
    if (loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    return RecordingBeanHome(
      darkMode: widget.darkMode,
      accent: widget.accent,
      setDarkMode: widget.setDarkMode,
      setAccent: widget.setAccent,
      session: session,
      authApi: authApi,
      backendAddress: backendAddress,
      authMessage: restoreMessage,
      onAuthenticated: _handleAuthenticated,
      onLogout: _logout,
      onChangePassword: _changePassword,
    );
  }
}

class _LoginPage extends StatefulWidget {
  const _LoginPage({
    required this.authApi,
    required this.backendAddress,
    required this.onAuthenticated,
    this.initialMessage = '',
  });

  final AuthApi authApi;
  final Uri? backendAddress;
  final String initialMessage;
  final Future<void> Function(AuthSession session) onAuthenticated;

  @override
  State<_LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<_LoginPage> {
  final formKey = GlobalKey<FormState>();
  final email = TextEditingController();
  final password = TextEditingController();
  final confirmation = TextEditingController();
  bool registerMode = false;
  bool obscurePassword = true;
  bool submitting = false;
  String error = '';

  @override
  void initState() {
    super.initState();
    error = widget.initialMessage;
  }

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    confirmation.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (widget.backendAddress == null ||
        !(formKey.currentState?.validate() ?? false) ||
        submitting) {
      return;
    }
    setState(() {
      submitting = true;
      error = '';
    });
    try {
      final session = registerMode
          ? await widget.authApi.register(
              widget.backendAddress!,
              email.text,
              password.text,
            )
          : await widget.authApi.login(
              widget.backendAddress!,
              email.text,
              password.text,
            );
      await widget.onAuthenticated(session);
      if (mounted) Navigator.of(context).pop();
    } catch (exception) {
      if (!mounted) return;
      setState(() => error = _authErrorText(exception));
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 430),
              child: Form(
                key: formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    CircleAvatar(
                      radius: 30,
                      backgroundColor: scheme.primaryContainer,
                      child: Icon(
                        Icons.graphic_eq,
                        size: 32,
                        color: scheme.onPrimaryContainer,
                      ),
                    ),
                    const SizedBox(height: 24),
                    Text(
                      registerMode ? '创建智能录音豆账户' : '登录智能录音豆',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.headlineSmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      registerMode ? '使用邮箱和密码创建账户' : '账户登录为可选功能，蓝牙配对和本地录音无需登录',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 28),
                    TextFormField(
                      controller: email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      decoration: const InputDecoration(
                        labelText: '邮箱',
                        prefixIcon: Icon(Icons.email_outlined),
                      ),
                      validator: (value) {
                        final text = value?.trim() ?? '';
                        if (text.isEmpty || !text.contains('@')) {
                          return '请输入有效的邮箱地址';
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: password,
                      obscureText: obscurePassword,
                      autofillHints: const [AutofillHints.password],
                      decoration: InputDecoration(
                        labelText: '密码',
                        prefixIcon: const Icon(Icons.lock_outline),
                        suffixIcon: IconButton(
                          onPressed: () => setState(
                            () => obscurePassword = !obscurePassword,
                          ),
                          tooltip: obscurePassword ? '显示密码' : '隐藏密码',
                          icon: Icon(
                            obscurePassword
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                          ),
                        ),
                      ),
                      validator: (value) {
                        if ((value ?? '').length < 8) {
                          return '密码至少需要 8 位';
                        }
                        return null;
                      },
                    ),
                    if (registerMode) ...[
                      const SizedBox(height: 14),
                      TextFormField(
                        controller: confirmation,
                        obscureText: obscurePassword,
                        decoration: const InputDecoration(
                          labelText: '确认密码',
                          prefixIcon: Icon(Icons.lock_reset_outlined),
                        ),
                        validator: (value) {
                          if (value != password.text) return '两次输入的密码不一致';
                          return null;
                        },
                      ),
                    ],
                    if (error.isNotEmpty) ...[
                      const SizedBox(height: 14),
                      Text(
                        error,
                        textAlign: TextAlign.center,
                        style: TextStyle(color: scheme.error),
                      ),
                    ],
                    const SizedBox(height: 22),
                    if (widget.backendAddress == null) ...[
                      Text(
                        '未配置账户服务。请返回主界面直接使用蓝牙录音；联网后可在设置中配置账户服务。',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: scheme.onSurfaceVariant),
                      ),
                      const SizedBox(height: 14),
                    ],
                    FilledButton(
                      onPressed: widget.backendAddress == null || submitting
                          ? null
                          : _submit,
                      child: submitting
                          ? const SizedBox.square(
                              dimension: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(registerMode ? '注册并进入' : '登录'),
                    ),
                    const SizedBox(height: 10),
                    TextButton(
                      onPressed: submitting
                          ? null
                          : () => setState(() {
                              registerMode = !registerMode;
                              error = '';
                            }),
                      child: Text(registerMode ? '已有账户？返回登录' : '没有账户？立即注册'),
                    ),
                    if (widget.backendAddress == null)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(
                          '尚未配置账户服务地址',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: scheme.error),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

String _authErrorText(Object exception) {
  if (exception is AuthException) return exception.message;
  if (exception is SocketException || exception is TimeoutException) {
    return '无法连接账户服务，请检查网络后重试。';
  }
  return '登录失败，请稍后重试。';
}

class RecordingBeanHome extends StatefulWidget {
  const RecordingBeanHome({
    super.key,
    required this.darkMode,
    required this.accent,
    required this.setDarkMode,
    required this.setAccent,
    required this.session,
    required this.authApi,
    required this.backendAddress,
    required this.authMessage,
    required this.onAuthenticated,
    required this.onLogout,
    required this.onChangePassword,
  });
  final bool darkMode;
  final AppearanceAccent accent;
  final Future<void> Function(bool value) setDarkMode;
  final Future<void> Function(AppearanceAccent value) setAccent;
  final AuthSession? session;
  final AuthApi authApi;
  final Uri? backendAddress;
  final String authMessage;
  final Future<void> Function(AuthSession session) onAuthenticated;
  final Future<void> Function() onLogout;
  final Future<void> Function(String currentPassword, String newPassword)
  onChangePassword;

  @override
  State<RecordingBeanHome> createState() => _RecordingBeanHomeState();
}

class _RecordingBeanHomeState extends State<RecordingBeanHome> {
  late final RecordingBeanController controller;

  @override
  void initState() {
    super.initState();
    controller = RecordingBeanController(authToken: widget.session?.token);
    unawaited(controller.initialize());
  }

  @override
  void didUpdateWidget(covariant RecordingBeanHome oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.session?.token != widget.session?.token) {
      controller.setAuthToken(widget.session?.token);
    }
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (!controller.initialized) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        return Scaffold(
          appBar: AppBar(
            title: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '智能录音豆',
                  style: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: Theme.of(context).colorScheme.onSurface,
                  ),
                ),
                Text(
                  controller.bleConnected ? '录音豆已连接' : '录音豆未连接',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
            actions: [
              Badge(
                isLabelVisible: controller.unifiedReleaseUpdateNeeded,
                backgroundColor: Theme.of(context).colorScheme.error,
                child: TextButton(
                  onPressed: () => _openMore(context),
                  child: const Text('更多'),
                ),
              ),
            ],
          ),
          body: IndexedStack(
            index: controller.pageIndex,
            children: [
              _ControlPage(controller: controller),
              _FilesPage(controller: controller),
              _AiSettingsPage(controller: controller),
            ],
          ),
          bottomNavigationBar: NavigationBar(
            selectedIndex: controller.pageIndex,
            onDestinationSelected: controller.setPage,
            destinations: const [
              NavigationDestination(
                icon: Icon(Icons.mic_outlined),
                selectedIcon: Icon(Icons.mic),
                label: '控制',
              ),
              NavigationDestination(
                icon: Icon(Icons.folder_open_outlined),
                selectedIcon: Icon(Icons.folder_open),
                label: '录音文件',
              ),
              NavigationDestination(icon: Icon(Icons.tune), label: '筛选设置'),
            ],
          ),
        );
      },
    );
  }

  Future<void> _openMore(BuildContext context) async {
    await Navigator.of(context).push<void>(
      PageRouteBuilder(
        pageBuilder: (context, animation, secondaryAnimation) => _MorePage(
          controller: controller,
          darkMode: widget.darkMode,
          accent: widget.accent,
          setDarkMode: widget.setDarkMode,
          setAccent: widget.setAccent,
          user: widget.session?.user,
          authApi: widget.authApi,
          backendAddress: widget.backendAddress,
          authMessage: widget.authMessage,
          onAuthenticated: (value) async {
            await widget.onAuthenticated(value);
            controller.setAuthToken(value.token);
          },
          onLogout: () async {
            await widget.onLogout();
            controller.setAuthToken(null);
          },
          onChangePassword: widget.onChangePassword,
        ),
        transitionsBuilder: (context, animation, secondaryAnimation, child) {
          final position = Tween<Offset>(
            begin: const Offset(-1, 0),
            end: Offset.zero,
          ).chain(CurveTween(curve: Curves.easeOutCubic));
          return SlideTransition(
            position: animation.drive(position),
            child: child,
          );
        },
      ),
    );
  }
}

class _MorePage extends StatefulWidget {
  const _MorePage({
    required this.controller,
    required this.darkMode,
    required this.accent,
    required this.setDarkMode,
    required this.setAccent,
    required this.user,
    required this.authApi,
    required this.backendAddress,
    required this.authMessage,
    required this.onAuthenticated,
    required this.onLogout,
    required this.onChangePassword,
  });

  final RecordingBeanController controller;
  final bool darkMode;
  final AppearanceAccent accent;
  final Future<void> Function(bool value) setDarkMode;
  final Future<void> Function(AppearanceAccent value) setAccent;
  final AuthUser? user;
  final AuthApi authApi;
  final Uri? backendAddress;
  final String authMessage;
  final Future<void> Function(AuthSession session) onAuthenticated;
  final Future<void> Function() onLogout;
  final Future<void> Function(String currentPassword, String newPassword)
  onChangePassword;

  @override
  State<_MorePage> createState() => _MorePageState();
}

class _MorePageState extends State<_MorePage> {
  final searchController = TextEditingController();
  String query = '';
  AuthUser? currentUser;
  late bool selectedDarkMode;
  late AppearanceAccent selectedAccent;

  static const sections = [
    _MoreSection(
      title: '账户',
      items: [_MoreItem('账户', Icons.account_circle_outlined)],
    ),
    _MoreSection(
      title: '集成',
      items: [
        _MoreItem('Obsidian 同步', Icons.sync_outlined),
        _MoreItem('ASR服务', Icons.record_voice_over_outlined),
        _MoreItem('云端存储', Icons.cloud_outlined),
      ],
    ),
    _MoreSection(
      title: '应用',
      items: [
        _MoreItem('外观', Icons.palette_outlined),
        _MoreItem('检查更新', Icons.system_update_outlined),
        _MoreItem('隐私与数据', Icons.privacy_tip_outlined),
        _MoreItem('关于智能录音豆', Icons.info_outline),
      ],
    ),
  ];

  @override
  void initState() {
    super.initState();
    currentUser = widget.user;
    selectedDarkMode = widget.darkMode;
    selectedAccent = widget.accent;
  }

  @override
  void dispose() {
    searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final normalizedQuery = query.trim().toLowerCase();
    final visibleSections = sections
        .map(
          (section) => _MoreSection(
            title: section.title,
            items: section.items
                .where(
                  (item) =>
                      normalizedQuery.isEmpty ||
                      item.label.toLowerCase().contains(normalizedQuery),
                )
                .toList(),
          ),
        )
        .where((section) => section.items.isNotEmpty)
        .toList();
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          onPressed: () => Navigator.of(context).pop(),
          tooltip: '返回应用',
          icon: const Icon(Icons.arrow_back),
        ),
        title: const Text('返回应用'),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              child: TextField(
                controller: searchController,
                onChanged: (value) => setState(() => query = value),
                decoration: const InputDecoration(
                  hintText: '搜索设置...',
                  prefixIcon: Icon(Icons.search),
                  isDense: true,
                ),
              ),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(8, 0, 8, 16),
                children: [
                  for (final section in visibleSections) ...[
                    Padding(
                      padding: const EdgeInsets.fromLTRB(12, 16, 12, 6),
                      child: Text(
                        section.title,
                        style: Theme.of(context).textTheme.labelLarge?.copyWith(
                          color: scheme.onSurfaceVariant,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    for (final item in section.items)
                      ListTile(
                        dense: true,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8),
                        ),
                        leading: Icon(item.icon, size: 20),
                        title: Text(item.label),
                        trailing:
                            item.label == '账户' ||
                                item.label == 'Obsidian 同步' ||
                                item.label == 'ASR服务' ||
                                item.label == '云端存储' ||
                                item.label == '外观' ||
                                item.label == '检查更新' ||
                                item.label == '隐私与数据'
                            ? const Icon(Icons.chevron_right, size: 18)
                            : null,
                        onTap: () => _selectItem(item),
                      ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _selectItem(_MoreItem item) async {
    if (item.label == '账户') {
      if (currentUser == null) {
        await Navigator.of(context).push<void>(
          MaterialPageRoute(
            builder: (context) => _LoginPage(
              authApi: widget.authApi,
              backendAddress: widget.backendAddress,
              initialMessage: widget.authMessage,
              onAuthenticated: (value) async {
                await widget.onAuthenticated(value);
                if (mounted) setState(() => currentUser = value.user);
              },
            ),
          ),
        );
        return;
      }
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => _AccountOverviewPage(
            user: currentUser!,
            controller: widget.controller,
            onLogout: widget.onLogout,
            onChangePassword: widget.onChangePassword,
          ),
        ),
      );
      return;
    }
    if (item.label == 'ASR服务') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => _AsrServicePage(controller: widget.controller),
        ),
      );
      return;
    }
    if (item.label == 'Obsidian 同步') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              _ObsidianSyncPage(controller: widget.controller),
        ),
      );
      return;
    }
    if (item.label == '云端存储') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              _CloudStoragePage(controller: widget.controller),
        ),
      );
      return;
    }
    if (item.label == '检查更新') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => _UpdatePage(controller: widget.controller),
        ),
      );
      return;
    }
    if (item.label == '外观') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => _AppearancePage(
            darkMode: selectedDarkMode,
            accent: selectedAccent,
            setDarkMode: (value) async {
              await widget.setDarkMode(value);
              if (mounted) setState(() => selectedDarkMode = value);
            },
            setAccent: (value) async {
              await widget.setAccent(value);
              if (mounted) setState(() => selectedAccent = value);
            },
          ),
        ),
      );
      return;
    }
    if (item.label == '隐私与数据') {
      await Navigator.of(context).push<void>(
        MaterialPageRoute(builder: (context) => const PrivacyPage()),
      );
      return;
    }
    if (item.label == '关于智能录音豆') {
      await Navigator.of(
        context,
      ).push<void>(MaterialPageRoute(builder: (context) => const _AboutPage()));
      return;
    }
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('${item.label}功能将在后续版本开放')));
  }
}

class _AccountOverviewPage extends StatelessWidget {
  const _AccountOverviewPage({
    required this.user,
    required this.controller,
    required this.onLogout,
    required this.onChangePassword,
  });

  final AuthUser user;
  final RecordingBeanController controller;
  final Future<void> Function() onLogout;
  final Future<void> Function(String currentPassword, String newPassword)
  onChangePassword;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final scheme = Theme.of(context).colorScheme;
        final initial = user.email.isEmpty ? '?' : user.email[0].toUpperCase();
        final deviceId = controller.accountDeviceId;
        final deviceName = controller.accountDeviceName;
        return Scaffold(
          appBar: AppBar(title: const Text('账户')),
          body: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(18),
                  child: Row(
                    children: [
                      CircleAvatar(
                        radius: 28,
                        backgroundColor: scheme.primaryContainer,
                        child: Text(
                          initial,
                          style: TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.w700,
                            color: scheme.onPrimaryContainer,
                          ),
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              user.email,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.titleMedium
                                  ?.copyWith(fontWeight: FontWeight.w700),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              '已登录',
                              style: Theme.of(context).textTheme.bodySmall
                                  ?.copyWith(color: scheme.onSurfaceVariant),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 18),
              _SectionTitle(title: '账户信息'),
              const SizedBox(height: 6),
              ListTile(
                leading: const Icon(Icons.email_outlined),
                title: const Text('邮箱'),
                subtitle: Text(user.email),
              ),
              ListTile(
                leading: const Icon(Icons.lock_outline),
                title: const Text('修改密码'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () async {
                  final changed = await Navigator.of(context).push<bool>(
                    MaterialPageRoute(
                      builder: (context) => _ChangePasswordPage(
                        onChangePassword: onChangePassword,
                      ),
                    ),
                  );
                  if (changed == true && context.mounted) {
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(const SnackBar(content: Text('密码已修改')));
                  }
                },
              ),
              const SizedBox(height: 18),
              _SectionTitle(title: '我的设备'),
              const SizedBox(height: 6),
              ListTile(
                leading: const Icon(Icons.memory_outlined),
                title: const Text('设备'),
                subtitle: Text(deviceName.isEmpty ? '尚未连接设备' : deviceName),
              ),
              ListTile(
                leading: const Icon(Icons.tag_outlined),
                title: const Text('设备 ID'),
                subtitle: Text(
                  deviceId.isEmpty ? '连接设备后显示' : deviceId,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(height: 18),
              OutlinedButton.icon(
                onPressed: () async {
                  await onLogout();
                  if (context.mounted) {
                    Navigator.of(context).popUntil((route) => route.isFirst);
                  }
                },
                icon: const Icon(Icons.logout),
                label: const Text('退出登录'),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle({required this.title});

  final String title;

  @override
  Widget build(BuildContext context) {
    return Text(
      title,
      style: Theme.of(context).textTheme.labelLarge?.copyWith(
        color: Theme.of(context).colorScheme.onSurfaceVariant,
        fontWeight: FontWeight.w700,
      ),
    );
  }
}

class _ChangePasswordPage extends StatefulWidget {
  const _ChangePasswordPage({required this.onChangePassword});

  final Future<void> Function(String currentPassword, String newPassword)
  onChangePassword;

  @override
  State<_ChangePasswordPage> createState() => _ChangePasswordPageState();
}

class _ChangePasswordPageState extends State<_ChangePasswordPage> {
  final formKey = GlobalKey<FormState>();
  final currentPassword = TextEditingController();
  final newPassword = TextEditingController();
  final confirmation = TextEditingController();
  bool obscurePassword = true;
  bool submitting = false;
  String error = '';

  @override
  void dispose() {
    currentPassword.dispose();
    newPassword.dispose();
    confirmation.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (submitting || !(formKey.currentState?.validate() ?? false)) return;
    setState(() {
      submitting = true;
      error = '';
    });
    try {
      await widget.onChangePassword(currentPassword.text, newPassword.text);
      if (mounted) Navigator.of(context).pop(true);
    } catch (exception) {
      if (mounted) setState(() => error = _authErrorText(exception));
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('修改密码')),
      body: SafeArea(
        child: Form(
          key: formKey,
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              _PasswordField(
                controller: currentPassword,
                label: '当前密码',
                autofillHints: const [AutofillHints.password],
                obscureText: obscurePassword,
                onToggleObscure: () =>
                    setState(() => obscurePassword = !obscurePassword),
                validator: (value) => (value ?? '').isEmpty ? '请输入当前密码' : null,
              ),
              const SizedBox(height: 14),
              _PasswordField(
                controller: newPassword,
                label: '新密码',
                autofillHints: const [AutofillHints.newPassword],
                obscureText: obscurePassword,
                onToggleObscure: () =>
                    setState(() => obscurePassword = !obscurePassword),
                validator: (value) =>
                    (value ?? '').length < 8 ? '新密码至少需要 8 位' : null,
              ),
              const SizedBox(height: 14),
              _PasswordField(
                controller: confirmation,
                label: '确认新密码',
                obscureText: obscurePassword,
                onToggleObscure: () =>
                    setState(() => obscurePassword = !obscurePassword),
                validator: (value) =>
                    value != newPassword.text ? '两次输入的密码不一致' : null,
              ),
              if (error.isNotEmpty) ...[
                const SizedBox(height: 14),
                Text(
                  error,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const SizedBox(height: 22),
              FilledButton(
                onPressed: submitting ? null : _submit,
                child: submitting
                    ? const SizedBox.square(
                        dimension: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('确认修改'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PasswordField extends StatelessWidget {
  const _PasswordField({
    required this.controller,
    required this.label,
    required this.obscureText,
    required this.onToggleObscure,
    required this.validator,
    this.autofillHints,
  });

  final TextEditingController controller;
  final String label;
  final bool obscureText;
  final VoidCallback onToggleObscure;
  final String? Function(String?) validator;
  final Iterable<String>? autofillHints;

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: controller,
      obscureText: obscureText,
      autofillHints: autofillHints,
      decoration: InputDecoration(
        labelText: label,
        prefixIcon: const Icon(Icons.lock_outline),
        suffixIcon: IconButton(
          onPressed: onToggleObscure,
          tooltip: obscureText ? '显示密码' : '隐藏密码',
          icon: Icon(
            obscureText
                ? Icons.visibility_outlined
                : Icons.visibility_off_outlined,
          ),
        ),
      ),
      validator: validator,
    );
  }
}

class _AppearancePage extends StatefulWidget {
  const _AppearancePage({
    required this.darkMode,
    required this.accent,
    required this.setDarkMode,
    required this.setAccent,
  });

  final bool darkMode;
  final AppearanceAccent accent;
  final Future<void> Function(bool value) setDarkMode;
  final Future<void> Function(AppearanceAccent value) setAccent;

  @override
  State<_AppearancePage> createState() => _AppearancePageState();
}

class _AppearancePageState extends State<_AppearancePage> {
  late bool darkMode;
  late AppearanceAccent accent;

  @override
  void initState() {
    super.initState();
    darkMode = widget.darkMode;
    accent = widget.accent;
  }

  @override
  void didUpdateWidget(covariant _AppearancePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.darkMode != widget.darkMode) darkMode = widget.darkMode;
    if (oldWidget.accent != widget.accent) accent = widget.accent;
  }

  Future<void> _setDarkMode(bool value) async {
    if (darkMode == value) return;
    setState(() => darkMode = value);
    await widget.setDarkMode(value);
  }

  Future<void> _setAccent(AppearanceAccent value) async {
    if (accent == value) return;
    setState(() => accent = value);
    await widget.setAccent(value);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('外观')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          Text(
            '当前外观',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          _AppearancePreview(darkMode: darkMode, accent: accent),
          const SizedBox(height: 24),
          Text(
            '显示模式',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment<bool>(
                value: false,
                icon: Icon(Icons.light_mode_outlined),
                label: Text('浅色'),
              ),
              ButtonSegment<bool>(
                value: true,
                icon: Icon(Icons.dark_mode_outlined),
                label: Text('深色'),
              ),
            ],
            selected: {darkMode},
            onSelectionChanged: (values) {
              if (values.isNotEmpty) _setDarkMode(values.first);
            },
          ),
          const SizedBox(height: 24),
          Text(
            '强调色',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              for (final value in AppearanceAccent.values)
                _AccentSwatch(
                  accent: value,
                  selected: accent == value,
                  onSelected: () => _setAccent(value),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _AppearancePreview extends StatelessWidget {
  const _AppearancePreview({required this.darkMode, required this.accent});

  final bool darkMode;
  final AppearanceAccent accent;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final surface = darkMode ? const Color(0xFF171D1A) : Colors.white;
    final background = darkMode ? const Color(0xFF101512) : canvas;
    final muted = darkMode ? const Color(0xFFB8C6BE) : const Color(0xFF5A655E);
    final accentTone = accent.color.withAlpha(darkMode ? 56 : 28);

    return Semantics(
      label: '${darkMode ? '深色' : '浅色'} ${accent.label}外观预览',
      child: Container(
        height: 198,
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: scheme.outlineVariant),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(7),
          child: Column(
            children: [
              Container(
                height: 46,
                padding: const EdgeInsets.symmetric(horizontal: 14),
                color: surface,
                child: Row(
                  children: [
                    Text(
                      '智能录音豆',
                      style: TextStyle(
                        color: darkMode
                            ? Colors.white
                            : const Color(0xFF17201B),
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const Spacer(),
                    Container(
                      width: 24,
                      height: 24,
                      decoration: BoxDecoration(
                        color: accentTone,
                        shape: BoxShape.circle,
                      ),
                      child: Icon(
                        Icons.graphic_eq,
                        size: 16,
                        color: accent.color,
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        height: 32,
                        padding: const EdgeInsets.symmetric(horizontal: 9),
                        decoration: BoxDecoration(
                          color: accentTone,
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.bluetooth_connected,
                              size: 15,
                              color: accent.color,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              '录音豆已连接',
                              style: TextStyle(
                                color: darkMode
                                    ? const Color(0xFFE2ECE5)
                                    : const Color(0xFF23332A),
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 10),
                      Container(
                        height: 10,
                        width: 122,
                        decoration: BoxDecoration(
                          color: muted.withAlpha(130),
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                      const SizedBox(height: 7),
                      Container(
                        height: 10,
                        width: 82,
                        decoration: BoxDecoration(
                          color: muted.withAlpha(80),
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                      const Spacer(),
                      SizedBox(
                        width: 120,
                        height: 32,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: accent.color,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: const Center(
                            child: Text(
                              '开始录音',
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              Container(
                height: 42,
                color: surface,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    Icon(Icons.mic, size: 19, color: accent.color),
                    Icon(Icons.folder_open_outlined, size: 19, color: muted),
                    Icon(Icons.tune, size: 19, color: muted),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AccentSwatch extends StatelessWidget {
  const _AccentSwatch({
    required this.accent,
    required this.selected,
    required this.onSelected,
  });

  final AppearanceAccent accent;
  final bool selected;
  final VoidCallback onSelected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Semantics(
      button: true,
      selected: selected,
      label: '选择${accent.label}',
      child: Tooltip(
        message: accent.label,
        child: InkWell(
          onTap: onSelected,
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(
            width: 72,
            child: Column(
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 160),
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    color: accent.color,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: selected
                          ? scheme.onSurface
                          : scheme.outlineVariant,
                      width: selected ? 3 : 1,
                    ),
                  ),
                  child: selected
                      ? const Icon(Icons.check, color: Colors.white)
                      : null,
                ),
                const SizedBox(height: 6),
                Text(
                  accent.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: selected
                        ? scheme.onSurface
                        : scheme.onSurfaceVariant,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _AboutPage extends StatefulWidget {
  const _AboutPage();

  @override
  State<_AboutPage> createState() => _AboutPageState();
}

class _AboutPageState extends State<_AboutPage> {
  bool chinese = true;
  String version = '--';

  @override
  void initState() {
    super.initState();
    unawaited(_loadVersion());
  }

  Future<void> _loadVersion() async {
    try {
      final package = await PackageInfo.fromPlatform();
      if (mounted) setState(() => version = package.version);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final title = chinese ? '关于智能录音豆' : 'About';
    final appName = chinese ? '智能录音豆' : 'AI Recording Bean';
    final description = chinese
        ? '用于连接和管理智能录音豆，完成录音、转写与同步。'
        : 'Connect and manage AI Recording Bean for recording, transcription, and sync.';
    final versionLabel = chinese ? '当前版本' : 'Version';
    final featuresLabel = chinese ? '核心功能' : 'Core features';
    final features = chinese
        ? '录音豆连接、录音文件管理、AI 转写、Obsidian 同步'
        : 'Recorder connection, recording management, AI transcription, and Obsidian sync';
    final settingsLabel = chinese ? '服务设置' : 'Service settings';
    final settings = chinese
        ? '在“更多”中配置 ASR 服务、Obsidian 同步和应用更新'
        : 'Configure ASR, Obsidian sync, and app updates in More';

    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 22, 16, 24),
        children: [
          Center(
            child: Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: scheme.primaryContainer,
                shape: BoxShape.circle,
              ),
              child: Icon(
                Icons.graphic_eq,
                color: scheme.onPrimaryContainer,
                size: 34,
              ),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            appName,
            textAlign: TextAlign.center,
            style: Theme.of(
              context,
            ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            description,
            textAlign: TextAlign.center,
            style: Theme.of(
              context,
            ).textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 22),
          Center(
            child: SegmentedButton<bool>(
              segments: const [
                ButtonSegment<bool>(value: true, label: Text('中文')),
                ButtonSegment<bool>(value: false, label: Text('English')),
              ],
              selected: {chinese},
              onSelectionChanged: (values) {
                if (values.isNotEmpty) {
                  setState(() => chinese = values.first);
                }
              },
            ),
          ),
          const SizedBox(height: 24),
          _AboutDetail(
            icon: Icons.verified_outlined,
            label: versionLabel,
            value: version,
          ),
          const Divider(height: 24),
          _AboutDetail(
            icon: Icons.auto_awesome_outlined,
            label: featuresLabel,
            value: features,
          ),
          const Divider(height: 24),
          _AboutDetail(
            icon: Icons.tune_outlined,
            label: settingsLabel,
            value: settings,
          ),
        ],
      ),
    );
  }
}

class _AboutDetail extends StatelessWidget {
  const _AboutDetail({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 20, color: scheme.primary),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  color: scheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 3),
              Text(value, style: Theme.of(context).textTheme.bodyLarge),
            ],
          ),
        ),
      ],
    );
  }
}

class _UpdatePage extends StatefulWidget {
  const _UpdatePage({required this.controller});

  final RecordingBeanController controller;

  @override
  State<_UpdatePage> createState() => _UpdatePageState();
}

class _UpdatePageState extends State<_UpdatePage> {
  String currentAppVersion = '--';

  @override
  void initState() {
    super.initState();
    unawaited(_loadAndCheck());
  }

  Future<void> _loadAndCheck() async {
    try {
      final package = await PackageInfo.fromPlatform();
      if (mounted) setState(() => currentAppVersion = package.version);
    } catch (_) {}
    await widget.controller.checkUnifiedRelease();
  }

  Future<void> _installUpdate() async {
    try {
      await widget.controller.installUnifiedUpdate();
      if (!mounted) return;
      final message = widget.controller.unifiedAppUpdateNeeded
          ? '已打开应用更新下载页面'
          : '已向录音豆发送固件更新请求';
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    } catch (exception) {
      if (mounted) {
        final detail = exception is AuthException
            ? exception.message
            : exception
                  .toString()
                  .replaceFirst('Bad state: ', '')
                  .replaceFirst('Exception: ', '');
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('启动更新失败：$detail')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final release = controller.unifiedRelease;
        final scheme = Theme.of(context).colorScheme;
        final busy =
            controller.unifiedReleaseInstallingApp ||
            (controller.bleOtaRequested &&
                controller.unifiedFirmwareUpdateNeeded);
        final deviceFirmware = controller.bleFirmware.trim();
        final hasUpdate = controller.unifiedReleaseUpdateNeeded;

        return Scaffold(
          appBar: AppBar(
            title: const Text('检查更新'),
            actions: [
              IconButton(
                onPressed: controller.unifiedReleaseChecking
                    ? null
                    : controller.checkUnifiedRelease,
                tooltip: '重新检查',
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          body: ListView(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
            children: [
              Row(
                children: [
                  if (controller.unifiedReleaseChecking)
                    const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  else
                    Icon(
                      controller.unifiedReleaseLastCheckSucceeded
                          ? hasUpdate
                                ? Icons.system_update_outlined
                                : Icons.check_circle_outline
                          : Icons.error_outline,
                      color: controller.unifiedReleaseLastCheckSucceeded
                          ? hasUpdate
                                ? scheme.primary
                                : scheme.primary
                          : scheme.error,
                    ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      controller.unifiedReleaseMessage,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 22),
              _SectionTitle(title: '应用'),
              const SizedBox(height: 6),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.phone_android_outlined),
                title: const Text('当前版本'),
                subtitle: Text('v$currentAppVersion'),
              ),
              if (release != null)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.new_releases_outlined),
                  title: const Text('可用版本'),
                  subtitle: Text('v${release.appVersion}'),
                ),
              const SizedBox(height: 18),
              _SectionTitle(title: '录音豆固件'),
              const SizedBox(height: 6),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.memory_outlined),
                title: const Text('当前版本'),
                subtitle: Text(
                  deviceFirmware.isEmpty ? '连接设备后显示' : 'v$deviceFirmware',
                ),
              ),
              if (release != null)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.new_releases_outlined),
                  title: const Text('可用版本'),
                  subtitle: Text('v${release.firmwareVersion}'),
                ),
              if (controller.bleOtaStatus.isNotEmpty)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.info_outline),
                  title: const Text('设备更新状态'),
                  subtitle: Text(controller.bleOtaStatus),
                ),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: controller.unifiedReleaseChecking || busy
                      ? null
                      : hasUpdate
                      ? _installUpdate
                      : controller.checkUnifiedRelease,
                  icon: busy
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Icon(
                          hasUpdate ? Icons.download_outlined : Icons.refresh,
                        ),
                  label: Text(
                    busy
                        ? '正在启动更新'
                        : hasUpdate
                        ? controller.unifiedAppUpdateNeeded
                              ? '下载应用更新'
                              : '安装固件更新'
                        : '重新检查',
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _MoreSection {
  const _MoreSection({required this.title, required this.items});

  final String title;
  final List<_MoreItem> items;
}

class _MoreItem {
  const _MoreItem(this.label, this.icon);

  final String label;
  final IconData icon;
}

class _AiSettingsPage extends StatefulWidget {
  const _AiSettingsPage({required this.controller});
  final RecordingBeanController controller;
  @override
  State<_AiSettingsPage> createState() => _AiSettingsPageState();
}

class _AiSettingsPageState extends State<_AiSettingsPage> {
  final custom = TextEditingController(), excluded = TextEditingController();
  bool loaded = false;
  @override
  void dispose() {
    custom.dispose();
    excluded.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller, p = c.workFilterProfile;
    if (!loaded) {
      loaded = true;
      custom.text = p.customDescription;
      excluded.text = p.excludedContent;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        c.loadWorkFilter();
      });
    }
    if (custom.text.isEmpty && p.customDescription.isNotEmpty) {
      custom.text = p.customDescription;
    }
    if (excluded.text.isEmpty && p.excludedContent.isNotEmpty) {
      excluded.text = p.excludedContent;
    }
    final types = c.workFilterTypes.isEmpty
        ? const {
            'software_development': '软件开发',
            'product': '产品管理',
            'project': '项目管理',
            'sales': '销售',
            'marketing': '市场运营',
            'customer_service': '客户服务',
            'hr_admin': '行政人事',
            'finance': '财务',
            'education': '教育培训',
            'healthcare': '医疗健康',
          }
        : c.workFilterTypes;
    final filtering = p.enabled;
    final allContent = p.recordingScene == 'all';
    final sceneDescription = switch (p.recordingScene) {
      'meeting' => '保留议题、汇报、讨论、决策、待办和下一步计划。',
      'all' => '保留优化后的完整文字，不做内容筛选。',
      _ => '只保留与所选工作类型和自定义描述直接相关的内容。',
    };
    final detailTitle = p.recordingScene == 'meeting'
        ? '会议重点（可多选）'
        : '工作类型（可多选）';
    final customLabel = p.recordingScene == 'meeting' ? '会议重点补充' : '我的工作内容';
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      children: [
        Text('筛选设置', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 4),
        Text('所有新录音统一使用这套设置', style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 12),
        Card(
          child: SwitchListTile(
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 4,
            ),
            title: const Text('工作内容筛选'),
            subtitle: Text(filtering ? '已开启：按记录场景处理' : '已关闭：写入优化后的完整转写'),
            value: filtering,
            onChanged: c.workFilterLoading
                ? null
                : (v) {
                    setState(
                      () => c.workFilterProfile = p.copyWith(enabled: v),
                    );
                    if (!v) unawaited(c.saveWorkFilter(c.workFilterProfile));
                  },
          ),
        ),
        const SizedBox(height: 12),
        Opacity(
          opacity: filtering ? 1 : 0.5,
          child: IgnorePointer(
            ignoring: !filtering,
            child: Card(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      '记录场景',
                      style: TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 10),
                    SegmentedButton<String>(
                      segments: const [
                        ButtonSegment<String>(
                          value: 'work',
                          label: Text('普通工作'),
                        ),
                        ButtonSegment<String>(
                          value: 'meeting',
                          label: Text('会议内容'),
                        ),
                        ButtonSegment<String>(
                          value: 'all',
                          label: Text('全部内容'),
                        ),
                      ],
                      selected: {p.recordingScene},
                      onSelectionChanged: (values) {
                        if (values.isNotEmpty) {
                          setState(
                            () => c.workFilterProfile = p.copyWith(
                              recordingScene: values.first,
                            ),
                          );
                        }
                      },
                    ),
                    const SizedBox(height: 9),
                    Text(
                      sceneDescription,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
        if (!filtering) ...[
          const SizedBox(height: 12),
          Card(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: const ListTile(
              leading: Icon(Icons.article_outlined),
              title: Text('当前不会筛选内容'),
              subtitle: Text('文字优化仍会自动执行，结果将以完整文字写入 Obsidian。'),
            ),
          ),
        ],
        if (filtering && !allContent) ...[
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    detailTitle,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    children: types.entries.map((e) {
                      final selected = p.workTypes.contains(e.key);
                      return FilterChip(
                        label: Text(e.value),
                        selected: selected,
                        onSelected: (v) {
                          final next = {...p.workTypes};
                          v ? next.add(e.key) : next.remove(e.key);
                          setState(
                            () => c.workFilterProfile = p.copyWith(
                              workTypes: next,
                            ),
                          );
                        },
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: custom,
                    maxLines: 3,
                    decoration: InputDecoration(
                      labelText: customLabel,
                      hintText: p.recordingScene == 'meeting'
                          ? '例如：重点关注决策、负责人和截止时间'
                          : '例如：嵌入式固件、Flutter App、后端接口开发',
                    ),
                    onChanged: (v) => c.workFilterProfile = c.workFilterProfile
                        .copyWith(customDescription: v),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: excluded,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: '明确排除的内容',
                      hintText: '例如：家庭聊天、购物、娱乐',
                    ),
                    onChanged: (v) => c.workFilterProfile = c.workFilterProfile
                        .copyWith(excludedContent: v),
                  ),
                ],
              ),
            ),
          ),
        ],
        if (filtering && allContent) ...[
          const SizedBox(height: 12),
          Card(
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: const ListTile(
              leading: Icon(Icons.notes_outlined),
              title: Text('完整文字模式'),
              subtitle: Text('工作类型和自定义描述不会参与筛选。'),
            ),
          ),
        ],
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: c.workFilterLoading
              ? null
              : () => c.saveWorkFilter(c.workFilterProfile),
          icon: c.workFilterLoading
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.save_outlined),
          label: const Text('保存设置'),
        ),
        if (c.workFilterMessage.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              c.workFilterMessage,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
      ],
    );
  }
}

class _ControlPage extends StatelessWidget {
  const _ControlPage({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: RefreshIndicator(
        onRefresh: controller.refreshControlPage,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 20),
          children: [
            if (controller.bleConnected &&
                controller.unifiedFirmwareUpdateNeeded) ...[
              _FirmwareUpdateBanner(controller: controller),
              const SizedBox(height: 10),
            ],
            if (controller.downloadingFileName.isNotEmpty) ...[
              _DownloadProgressPanel(controller: controller),
              const SizedBox(height: 10),
            ],
            _WorkflowPanel(controller: controller),
            const SizedBox(height: 10),
            _BlePanel(controller: controller),
            const SizedBox(height: 10),
            _RecordingStatusPanel(controller: controller),
            const SizedBox(height: 10),
            _LiveTranscriptionPanel(controller: controller),
            const SizedBox(height: 10),
            _TranscriptionPanel(controller: controller),
          ],
        ),
      ),
    );
  }
}

enum _WorkflowState { done, active, waiting, failed }

/// 控制页顶部横幅：设备固件有新版本时提示，点击进入检查更新页。
class _FirmwareUpdateBanner extends StatelessWidget {
  const _FirmwareUpdateBanner({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final version = controller.unifiedRelease?.firmwareVersion ?? '';
    return Card(
      color: scheme.primaryContainer,
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () {
          Navigator.of(context).push<void>(
            MaterialPageRoute(
              builder: (context) => _UpdatePage(controller: controller),
            ),
          );
        },
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            children: [
              Icon(Icons.system_update_outlined, color: scheme.onPrimaryContainer),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  version.isEmpty
                      ? '录音豆固件有新版本，点击前往更新'
                      : '录音豆固件有新版本 v$version，点击前往更新',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: scheme.onPrimaryContainer,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              Icon(Icons.chevron_right, color: scheme.onPrimaryContainer),
            ],
          ),
        ),
      ),
    );
  }
}

class _WorkflowStep {
  const _WorkflowStep(this.label, this.state);
  final String label;
  final _WorkflowState state;
}

class _WorkflowPanel extends StatelessWidget {
  const _WorkflowPanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final job = controller.displayWorkflowJob;
    final status = job?.status ?? '';
    final uploadActive = controller.bleConnected
        ? controller.bleUploading
        : controller.connected && controller.status.uploadInProgress;
    final hasBackendJob = job != null;
    final transcriptionFailed = const {
      'failed',
      'transcription_failed',
      'missing',
    }.contains(status);
    final transcriptionActive = const {
      'pending',
      'processing',
      'transcribing',
    }.contains(status);
    final transcriptionDone = const {
      'note_pending',
      'note_processing',
      'note_failed',
      'ai_pending',
      'optimizing',
      'filtering',
      'ai_failed',
      'completed',
    }.contains(status);
    final aiActive = const {
      'ai_pending',
      'optimizing',
      'filtering',
    }.contains(status);
    final aiFailed = status == 'ai_failed';
    final aiDone = status == 'completed';
    final steps = [
      _WorkflowStep(
        '连接',
        controller.bleConnected || hasBackendJob
            ? _WorkflowState.done
            : controller.bleConnecting
            ? _WorkflowState.active
            : _WorkflowState.waiting,
      ),
      _WorkflowStep(
        '录音',
        controller.bleRecording
            ? _WorkflowState.active
            : hasBackendJob || controller.workflowFile.isNotEmpty
            ? _WorkflowState.done
            : _WorkflowState.waiting,
      ),
      _WorkflowStep(
        '上传',
        uploadActive
            ? _WorkflowState.active
            : hasBackendJob
            ? _WorkflowState.done
            : controller.workflowUploadStarted
            ? _WorkflowState.active
            : _WorkflowState.waiting,
      ),
      _WorkflowStep(
        '转写',
        transcriptionFailed
            ? _WorkflowState.failed
            : transcriptionDone
            ? _WorkflowState.done
            : transcriptionActive
            ? _WorkflowState.active
            : _WorkflowState.waiting,
      ),
      _WorkflowStep(
        'AI处理',
        aiFailed
            ? _WorkflowState.failed
            : aiDone
            ? _WorkflowState.done
            : aiActive
            ? _WorkflowState.active
            : _WorkflowState.waiting,
      ),
      _WorkflowStep(
        '笔记',
        aiDone && job?.noteSynced == true
            ? _WorkflowState.done
            : aiDone
            ? _WorkflowState.active
            : _WorkflowState.waiting,
      ),
    ];
    final durationText = _durationText(job);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (var index = 0; index < steps.length; index++) ...[
                  Expanded(child: _WorkflowNode(step: steps[index])),
                  if (index < steps.length - 1)
                    AnimatedContainer(
                      duration: const Duration(milliseconds: 300),
                      width: 10,
                      height: 3,
                      margin: const EdgeInsets.only(top: 9),
                      color: _workflowColor(context, steps[index].state),
                    ),
                ],
              ],
            ),
            const SizedBox(height: 10),
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Expanded(
                  child: Text(
                    _workflowMessage(job, uploadActive),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: aiFailed
                          ? Theme.of(context).colorScheme.error
                          : Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
                if (job != null && aiFailed && job.jobId > 0) ...[
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    onPressed: controller.aiRetryingJobIds.contains(job.jobId)
                        ? null
                        : () => _retryAiWithConsent(context, job),
                    icon: controller.aiRetryingJobIds.contains(job.jobId)
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.refresh, size: 18),
                    label: const Text('重试AI'),
                  ),
                ],
              ],
            ),
            if (durationText.isNotEmpty) ...[
              const SizedBox(height: 5),
              Text(
                durationText,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: Theme.of(context).colorScheme.outline,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  String _workflowMessage(TranscriptionJobStatus? job, bool uploadActive) {
    if (controller.bleRecording) {
      return '正在录音：${_formatDuration(controller.bleRecordingSeconds)}';
    }
    if (controller.workflowWritingToTfCard) return '正在保存录音';
    if (controller.bleUploading && controller.bleUploadTotalBytes > 0) {
      final percent =
          (controller.bleUploadSentBytes *
                  100 ~/
                  controller.bleUploadTotalBytes)
              .clamp(0, 100);
      return '正在上传录音：$percent%';
    }
    if (controller.bleUploading) return '正在上传录音';
    if (job == null) {
      if (uploadActive || controller.workflowUploadStarted) return '正在上传录音';
      return controller.bleConnected ? '等待开始录音' : '等待连接录音豆';
    }
    return switch (job.status) {
      'pending' => '上传完成，等待转写',
      'processing' || 'transcribing' => '正在转写录音${_elapsedText(job)}',
      'failed' || 'transcription_failed' => '录音转写失败，等待自动重试',
      'missing' => '云端录音已不存在，无法转写',
      'note_pending' ||
      'note_processing' => '转写完成，正在保存原文笔记${_elapsedText(job)}',
      'note_failed' => '原文已保存，笔记写入失败，等待重试',
      'ai_pending' => '原文笔记已保存，等待AI优化${_elapsedText(job)}',
      'optimizing' => '正在优化转写文字${_elapsedText(job)}',
      'filtering' => '优化稿已保存，正在筛选内容${_elapsedText(job)}',
      'ai_failed' => _aiFailureMessage(job),
      'completed' when job.noteSynced => 'AI处理完成，最终笔记已写入Obsidian',
      'completed' => 'AI处理完成，等待写入Obsidian',
      _ => '等待后台处理',
    };
  }

  Future<void> _retryAiWithConsent(
    BuildContext context,
    TranscriptionJobStatus job,
  ) async {
    if (!await ensurePrivacyConsent(context)) return;
    await controller.retryAiProcessing(job);
  }

  String _aiFailureMessage(TranscriptionJobStatus job) {
    final detail = job.aiError.trim().isNotEmpty
        ? job.aiError.trim()
        : job.error.trim();
    if (detail.isEmpty) return 'AI处理失败，原文笔记已保留';
    return 'AI处理失败：$detail（原文笔记已保留）';
  }

  String _durationText(TranscriptionJobStatus? job) {
    if (job == null) return '';
    final durations = job.durations;
    final parts = <String>[];
    void add(String label, double seconds) {
      if (seconds <= 0) return;
      parts.add('$label ${_formatSeconds(seconds)}');
    }

    add('下载', durations.download);
    add('转写', durations.asr);
    add('原文笔记', durations.rawNote);
    add('优化', durations.optimize);
    add('筛选', durations.filter);
    add('最终笔记', durations.finalNote);
    return parts.join(' · ');
  }

  String _elapsedText(TranscriptionJobStatus job) {
    final seconds = liveStageElapsedSeconds(job, DateTime.now());
    if (seconds <= 0) return '';
    return '（已用时 ${_formatSeconds(seconds)}）';
  }

  String _formatSeconds(double seconds) => formatElapsedSeconds(seconds);
}

class _WorkflowNode extends StatelessWidget {
  const _WorkflowNode({required this.step});
  final _WorkflowStep step;

  @override
  Widget build(BuildContext context) {
    final color = _workflowColor(context, step.state);
    return Column(
      children: [
        if (step.state == _WorkflowState.done)
          Icon(Icons.check_circle, size: 20, color: color)
        else
          Container(
            width: 20,
            height: 20,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            alignment: Alignment.center,
            child: step.state == _WorkflowState.active
                ? Container(
                    width: 7,
                    height: 7,
                    decoration: const BoxDecoration(
                      color: Colors.white,
                      shape: BoxShape.circle,
                    ),
                  )
                : null,
          ),
        const SizedBox(height: 3),
        Text(
          step.label,
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
            color: Theme.of(context).colorScheme.onSurface,
          ),
        ),
      ],
    );
  }
}

Color _workflowColor(BuildContext context, _WorkflowState state) =>
    switch (state) {
      _WorkflowState.done => Theme.of(context).colorScheme.primary,
      _WorkflowState.active => Theme.of(context).colorScheme.tertiary,
      _WorkflowState.failed => Theme.of(context).colorScheme.error,
      _WorkflowState.waiting => Theme.of(context).colorScheme.outlineVariant,
    };

class _BlePanel extends StatelessWidget {
  const _BlePanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final shortName = controller.bleDeviceName
        .replaceFirst('AI Recording Bean ', '')
        .trim();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            Row(
              children: [
                _StatusDot(active: controller.bleConnected),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    controller.bleConnected
                        ? '已连接${shortName.isEmpty ? '' : ' $shortName'}'
                        : controller.bleMessage,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurface,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                if (controller.bleConnected)
                  FilledButton.tonalIcon(
                    onPressed: controller.bleDisconnecting
                        ? null
                        : controller.disconnectBleDevice,
                    icon: const Icon(Icons.link_off, size: 18),
                    label: Text(controller.bleDisconnecting ? '断开中' : '断开'),
                  )
                else
                  FilledButton.icon(
                    onPressed:
                        controller.bleScanning || controller.bleConnecting
                        ? null
                        : controller.discoverBleDevices,
                    icon: controller.bleScanning || controller.bleConnecting
                        ? const SizedBox.square(
                            dimension: 17,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.bluetooth_searching, size: 19),
                    label: const Text('查找'),
                  ),
              ],
            ),
            for (final device in controller.bleDevices) ...[
              const SizedBox(height: 8),
              _DeviceButton(controller: controller, device: device),
            ],
            if (controller.bleConnected || controller.bleConnecting) ...[
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton.icon(
                  onPressed: controller.bleConnected && !controller.loading
                      ? () => _toggleRecording(context)
                      : null,
                  style: FilledButton.styleFrom(
                    backgroundColor: controller.bleRecording
                        ? danger
                        : Theme.of(context).colorScheme.primary,
                    foregroundColor: Colors.white,
                  ),
                  icon: Icon(
                    controller.bleRecording
                        ? Icons.stop_circle_outlined
                        : Icons.mic,
                  ),
                  label: Text(
                    controller.bleRecording
                        ? '停止录音'
                        : controller.bleConnected
                        ? '开始录音'
                        : '正在准备录音',
                    style: const TextStyle(fontSize: 17),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _toggleRecording(BuildContext context) async {
    if (!controller.bleRecording && !await ensurePrivacyConsent(context)) {
      return;
    }
    try {
      await controller.toggleRecording();
    } catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('录音操作失败：$error')));
    }
  }
}

class _DeviceButton extends StatelessWidget {
  const _DeviceButton({required this.controller, required this.device});
  final RecordingBeanController controller;
  final BleCandidate device;

  @override
  Widget build(BuildContext context) {
    final previous = controller.wasLastConnected(device);
    final name = device.name.replaceFirst('AI Recording Bean ', '').trim();
    return SizedBox(
      width: double.infinity,
      child: FilledButton.tonalIcon(
        onPressed: controller.bleConnecting
            ? null
            : () => controller.connectBleDevice(device),
        style: FilledButton.styleFrom(
          backgroundColor: previous
              ? Theme.of(context).colorScheme.primaryContainer
              : Theme.of(context).colorScheme.secondaryContainer,
        ),
        icon: const Icon(Icons.bluetooth),
        label: Text(
          '连接 ${name.isEmpty ? '录音豆' : name}${previous ? ' · 上次连过' : ''}',
        ),
      ),
    );
  }
}

/// 直播实时字幕卡片：录音中显示 sherpa 流式引擎的 partial 与已定稿句。
class _LiveTranscriptionPanel extends StatelessWidget {
  const _LiveTranscriptionPanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final recording = controller.bleConnected && controller.bleRecording;
    final show = recording && controller.liveActive;
    if (!show) {
      return const SizedBox.shrink();
    }
    final sentences = controller.liveSentences;
    final partial = controller.livePartial;
    final recent = sentences.length > 6
        ? sentences.sublist(sentences.length - 6)
        : sentences;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SizedBox.square(
                  dimension: 10,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      color: scheme.primary,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '实时转写中 · 已出 ${sentences.length} 句',
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (recent.isNotEmpty)
              ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 180),
                child: SingleChildScrollView(
                  reverse: true,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final sentence in recent)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Text(
                            sentence,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            if (partial.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  partial,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: scheme.onSurfaceVariant,
                        fontStyle: FontStyle.italic,
                      ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _RecordingStatusPanel extends StatelessWidget {
  const _RecordingStatusPanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final storage = controller.bleConnected
        ? _storageText(controller.bleSdFreeMb, controller.bleSdTotalMb)
        : _storageTextFromStatus(controller.status);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  controller.bleRecording ? Icons.graphic_eq : Icons.mic_none,
                  color: controller.bleRecording
                      ? Theme.of(context).colorScheme.error
                      : Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    controller.bleRecording
                        ? '正在录音 ${_formatDuration(controller.bleRecordingSeconds)}'
                        : '录音设备状态',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: Theme.of(context).colorScheme.onSurface,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (controller.bleCurrentFile.isNotEmpty)
              _InfoRow(label: '当前文件', value: controller.bleCurrentFile),
            _InfoRow(
              label: '固件版本',
              value: controller.bleFirmware.isEmpty
                  ? '--'
                  : controller.bleFirmware,
            ),
            if (storage.isNotEmpty) _InfoRow(label: '存储空间', value: storage),
          ],
        ),
      ),
    );
  }

  String _storageText(double? freeMb, double? totalMb) {
    if (freeMb == null || totalMb == null) return '';
    return '${freeMb.toStringAsFixed(0)} / ${totalMb.toStringAsFixed(0)} MB 可用';
  }

  String _storageTextFromStatus(DeviceStatus status) {
    if (status.sdFreeMb == null || status.sdTotalMb == null) return '';
    return '${status.sdFreeMb!.toStringAsFixed(0)} / ${status.sdTotalMb!.toStringAsFixed(0)} MB 可用';
  }
}

class _ObsidianSyncPage extends StatefulWidget {
  const _ObsidianSyncPage({required this.controller});

  final RecordingBeanController controller;

  @override
  State<_ObsidianSyncPage> createState() => _ObsidianSyncPageState();
}

class _ObsidianSyncPageState extends State<_ObsidianSyncPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      widget.controller.loadObsidianSync();
    });
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final syncStatus = controller.obsidianSyncStatus;
        final pairing = controller.obsidianSyncPairing;
        final pairingInfo = controller.obsidianSyncPairingInfo;
        final scheme = Theme.of(context).colorScheme;

        return Scaffold(
          appBar: AppBar(
            title: const Text('Obsidian 同步'),
            actions: [
              IconButton(
                onPressed: controller.obsidianSyncLoading
                    ? null
                    : controller.loadObsidianSync,
                tooltip: '刷新同步状态',
                icon: controller.obsidianSyncLoading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
              ),
            ],
          ),
          body: RefreshIndicator(
            onRefresh: controller.loadObsidianSync,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
              children: [
                if (syncStatus?.bound != true)
                  Card(
                    child: ListTile(
                      leading: Icon(Icons.link_outlined, color: scheme.outline),
                      title: const Text('同步正在准备中'),
                      subtitle: const Text('首次连接录音豆后会自动完成账户绑定'),
                    ),
                  )
                else ...[
                  const _SectionTitle(title: '当前输出电脑'),
                  const SizedBox(height: 6),
                  if ((syncStatus?.targets ?? const []).isEmpty)
                    Card(
                      child: const ListTile(
                        leading: Icon(Icons.computer_outlined),
                        title: Text('尚未连接 Obsidian'),
                      ),
                    )
                  else
                    for (final target in syncStatus!.targets) ...[
                      Card(
                        child: ListTile(
                          leading: Icon(
                            Icons.computer_outlined,
                            color: scheme.primary,
                          ),
                          title: Text(
                            target.label.isEmpty ? 'Obsidian' : target.label,
                          ),
                          subtitle: Text(
                            target.lastUsedAt.isEmpty
                                ? '已激活'
                                : '最近同步：${_formatTime(target.lastUsedAt)}',
                          ),
                        ),
                      ),
                      const SizedBox(height: 8),
                    ],
                  if (pairing != null) ...[
                    const SizedBox(height: 18),
                    const _SectionTitle(title: '等待新电脑激活'),
                    const SizedBox(height: 6),
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _CopyableSyncValue(
                              label: '激活码',
                              value: pairing.activationCode,
                              onCopy: () => _copy(
                                context,
                                pairing.activationCode,
                                '激活码已复制',
                              ),
                            ),
                            if (pairingInfo.isNotEmpty) ...[
                              const SizedBox(height: 14),
                              _CopyableSyncValue(
                                label: '配对信息',
                                value: pairingInfo,
                                onCopy: () =>
                                    _copy(context, pairingInfo, '配对信息已复制'),
                              ),
                            ],
                            const SizedBox(height: 10),
                            Text(
                              '有效至 ${_formatTime(pairing.expiresAt)}',
                              style: Theme.of(context).textTheme.bodySmall
                                  ?.copyWith(color: scheme.onSurfaceVariant),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                  const SizedBox(height: 22),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      onPressed: controller.obsidianSyncLoading
                          ? null
                          : () => _createPairing(context),
                      icon: controller.obsidianSyncLoading
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.swap_horiz),
                      label: const Text('切换输出电脑'),
                    ),
                  ),
                ],
                if (controller.obsidianSyncMessage.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      controller.obsidianSyncMessage,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _createPairing(BuildContext context) async {
    final targetName = await _targetName(context);
    if (targetName == null) return;
    try {
      await widget.controller.createObsidianSyncPairing(targetName);
    } catch (_) {}
  }

  Future<String?> _targetName(BuildContext context) async {
    final name = TextEditingController(text: '新电脑');
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('切换输出电脑'),
        content: TextField(
          controller: name,
          autofocus: true,
          decoration: const InputDecoration(labelText: '电脑名称'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(name.text.trim()),
            child: const Text('生成激活码'),
          ),
        ],
      ),
    );
    name.dispose();
    return result;
  }

  Future<void> _copy(BuildContext context, String value, String message) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (context.mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  String _formatTime(String value) {
    final parsed = DateTime.tryParse(value);
    if (parsed == null) return value.isEmpty ? '--' : value;
    final local = parsed.toLocal();
    final month = local.month.toString().padLeft(2, '0');
    final day = local.day.toString().padLeft(2, '0');
    final hour = local.hour.toString().padLeft(2, '0');
    final minute = local.minute.toString().padLeft(2, '0');
    return '${local.year}-$month-$day $hour:$minute';
  }
}

class _CopyableSyncValue extends StatelessWidget {
  const _CopyableSyncValue({
    required this.label,
    required this.value,
    required this.onCopy,
  });

  final String label;
  final String value;
  final VoidCallback onCopy;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: Theme.of(context).textTheme.labelLarge),
              const SizedBox(height: 4),
              SelectableText(value),
            ],
          ),
        ),
        IconButton(
          onPressed: onCopy,
          tooltip: '复制$label',
          icon: const Icon(Icons.content_copy_outlined),
        ),
      ],
    );
  }
}

class _CloudStoragePage extends StatefulWidget {
  const _CloudStoragePage({required this.controller});

  final RecordingBeanController controller;

  @override
  State<_CloudStoragePage> createState() => _CloudStoragePageState();
}

class _CloudStoragePageState extends State<_CloudStoragePage> {
  final formKey = GlobalKey<FormState>();
  late TextEditingController endpoint;
  late TextEditingController region;
  late TextEditingController bucket;
  late TextEditingController recordingsPrefix;
  late TextEditingController notesPrefix;
  late TextEditingController accessKey;
  late TextEditingController secretKey;
  late TextEditingController tlsCaCert;
  late TextEditingController adapterFactory;
  late TextEditingController adapterOptionsJson;
  String storageMode = 'backend-default';
  String provider = 'minio';
  String error = '';
  bool _initialConfigApplied = false;

  static const providers = {
    'minio': 'MinIO',
    'aliyun-oss': '阿里云 OSS',
    'huawei-obs': '华为云 OBS',
    'tencent-cos': '腾讯云 COS',
    'other-s3': '其他 S3',
  };

  @override
  void initState() {
    super.initState();
    endpoint = TextEditingController();
    region = TextEditingController();
    bucket = TextEditingController();
    recordingsPrefix = TextEditingController();
    notesPrefix = TextEditingController();
    accessKey = TextEditingController();
    secretKey = TextEditingController();
    tlsCaCert = TextEditingController();
    adapterFactory = TextEditingController();
    adapterOptionsJson = TextEditingController();
    _applyConfig(widget.controller.cloudStorageConfig);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await widget.controller.loadCloudStorage();
      if (!mounted) return;
      _applyConfig(widget.controller.cloudStorageConfig);
    });
  }

  @override
  void dispose() {
    for (final controller in [
      endpoint,
      region,
      bucket,
      recordingsPrefix,
      notesPrefix,
      accessKey,
      secretKey,
      tlsCaCert,
      adapterFactory,
      adapterOptionsJson,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  void _applyConfig(CloudStorageConfig config) {
    if (_initialConfigApplied && widget.controller.cloudStorageSaving) return;
    _initialConfigApplied = true;
    storageMode = config.usesBackendDefault
        ? 'backend-default'
        : config.type == 'custom'
        ? 'custom'
        : 's3';
    provider = providers.containsKey(config.provider)
        ? config.provider
        : 'minio';
    endpoint.text = config.endpoint;
    region.text = config.region;
    bucket.text = config.bucket;
    recordingsPrefix.text = config.recordingsPrefix;
    notesPrefix.text = config.notesPrefix;
    accessKey.text = '';
    secretKey.text = '';
    tlsCaCert.text = config.tlsCaCert;
    adapterFactory.text = config.adapterFactory;
    adapterOptionsJson.text = '';
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final scheme = Theme.of(context).colorScheme;
        final current = controller.cloudStorageConfig;
        final usesBackendDefault = storageMode == 'backend-default';
        final storedCustomCredentials =
            !current.usesBackendDefault &&
            current.accessKeyConfigured &&
            current.secretKeyConfigured;
        final canSyncCustomStorage =
            storageMode == 's3' &&
            controller.bleConnected &&
            accessKey.text.trim().isNotEmpty &&
            secretKey.text.trim().isNotEmpty;
        final https = endpoint.text.trim().startsWith('https://');
        return Scaffold(
          appBar: AppBar(
            title: const Text('云端存储'),
            actions: [
              IconButton(
                onPressed:
                    controller.cloudStorageLoading ||
                        controller.cloudStorageSaving
                    ? null
                    : () async {
                        await controller.loadCloudStorage();
                        if (mounted) {
                          _applyConfig(controller.cloudStorageConfig);
                        }
                      },
                tooltip: '刷新配置',
                icon: controller.cloudStorageLoading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
              ),
            ],
          ),
          body: Form(
            key: formKey,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
              children: [
                Card(
                  child: ListTile(
                    leading: Icon(
                      usesBackendDefault
                          ? Icons.lock_outline
                          : current.type == 'custom'
                          ? Icons.extension_outlined
                          : controller.bleCloudConfigured
                          ? Icons.cloud_done_outlined
                          : Icons.cloud_outlined,
                      color: controller.bleCloudConfigured
                          ? scheme.primary
                          : scheme.onSurfaceVariant,
                    ),
                    title: Text(
                      controller.cloudStorageMessage.isEmpty
                          ? usesBackendDefault
                                ? '后端默认 MinIO'
                                : current.type == 'custom'
                                ? '自定义适配器'
                                : 'S3 兼容存储'
                          : controller.cloudStorageMessage,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(
                      usesBackendDefault
                          ? '密钥仅保存在后端，不显示在手机端'
                          : controller.bleConnected
                          ? '录音豆：${controller.bleCloudConfigured ? '已配置' : '待配置'}'
                          : '录音豆未连接',
                    ),
                  ),
                ),
                const SizedBox(height: 18),
                const _SectionTitle(title: '存储类型'),
                const SizedBox(height: 6),
                SegmentedButton<String>(
                  segments: const [
                    ButtonSegment(
                      value: 'backend-default',
                      icon: Icon(Icons.lock_outline),
                      label: Text('后端默认'),
                    ),
                    ButtonSegment(
                      value: 's3',
                      icon: Icon(Icons.cloud_outlined),
                      label: Text('自定义 S3'),
                    ),
                    ButtonSegment(
                      value: 'custom',
                      icon: Icon(Icons.extension_outlined),
                      label: Text('自定义适配器'),
                    ),
                  ],
                  selected: {storageMode},
                  onSelectionChanged: controller.cloudStorageSaving
                      ? null
                      : (values) => setState(() {
                          storageMode = values.first;
                        }),
                ),
                const SizedBox(height: 14),
                if (usesBackendDefault) ...[
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.verified_user_outlined),
                    title: const Text('后端默认 MinIO'),
                    subtitle: Text(
                      current.endpoint.isEmpty
                          ? '由后端统一管理'
                          : '${current.endpoint}\n${current.bucket}/${current.recordingsPrefix}',
                    ),
                  ),
                  Text(
                    'Access Key 与 Secret Key 保存在后端，不会展示或下发到手机。',
                    style: TextStyle(color: scheme.onSurfaceVariant),
                  ),
                ] else if (storageMode == 's3') ...[
                  DropdownButtonFormField<String>(
                    initialValue: providers.containsKey(provider)
                        ? provider
                        : 'minio',
                    decoration: const InputDecoration(labelText: '服务厂商'),
                    items: [
                      for (final entry in providers.entries)
                        DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                    ],
                    onChanged: controller.cloudStorageSaving
                        ? null
                        : (value) => setState(() {
                            provider = value ?? 'minio';
                          }),
                  ),
                  const SizedBox(height: 10),
                  _storageField(
                    endpoint,
                    'Endpoint',
                    'https://storage.example.com',
                    keyboardType: TextInputType.url,
                    validator: _validateEndpoint,
                  ),
                  _storageField(
                    region,
                    '区域 Region',
                    '例如：us-east-1',
                    choices: const [
                      _StorageValueChoice('us-east-1', 'us-east-1'),
                    ],
                    validator: _required('请填写区域'),
                  ),
                  _storageField(
                    bucket,
                    'Bucket',
                    '例如：recordingbean',
                    validator: _required('请填写 Bucket'),
                  ),
                  _storageField(
                    recordingsPrefix,
                    '录音对象路径',
                    'recordings/',
                    choices: const [
                      _StorageValueChoice('recordings/', 'recordings/'),
                    ],
                    validator: _validatePrefix,
                  ),
                  _storageField(
                    notesPrefix,
                    '笔记对象路径',
                    'notes/',
                    choices: const [_StorageValueChoice('notes/', 'notes/')],
                    validator: _validatePrefix,
                  ),
                  _storageField(
                    accessKey,
                    'Access Key',
                    storedCustomCredentials ? '已配置，留空保持原值' : '请输入 Access Key',
                    obscureText: true,
                    validator: storedCustomCredentials
                        ? null
                        : _required('请填写 Access Key'),
                  ),
                  _storageField(
                    secretKey,
                    'Secret Key',
                    storedCustomCredentials ? '已配置，留空保持原值' : '请输入 Secret Key',
                    obscureText: true,
                    validator: storedCustomCredentials
                        ? null
                        : _required('请填写 Secret Key'),
                  ),
                  ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    childrenPadding: const EdgeInsets.only(top: 4),
                    title: const Text('TLS 证书'),
                    subtitle: Text(
                      https ? 'HTTPS 设备直连需要 CA PEM' : 'HTTP 不需要填写',
                    ),
                    children: [
                      _storageField(
                        tlsCaCert,
                        'TLS CA PEM',
                        '-----BEGIN CERTIFICATE-----',
                        maxLines: 6,
                        validator: https && tlsCaCert.text.trim().isEmpty
                            ? (_) => 'HTTPS 设备直连必须填写 CA PEM'
                            : null,
                      ),
                    ],
                  ),
                  if (storedCustomCredentials)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        '密钥已安全保存在后端。若要同步这套配置到录音豆，请重新填写两项密钥。',
                        style: TextStyle(color: scheme.onSurfaceVariant),
                      ),
                    ),
                ] else ...[
                  _storageField(
                    adapterFactory,
                    '适配器工厂',
                    'package.module:create_store',
                    validator: _required('请填写适配器工厂'),
                  ),
                  _storageField(
                    adapterOptionsJson,
                    '适配器配置 JSON',
                    current.adapterOptionsConfigured
                        ? '已配置，留空保持原值'
                        : '{"endpoint": "..."}',
                    maxLines: 6,
                    validator: _validateJson,
                  ),
                ],
                if (error.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(error, style: TextStyle(color: scheme.error)),
                ],
                const SizedBox(height: 18),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed:
                            controller.cloudStorageTesting ||
                                controller.cloudStorageSaving
                            ? null
                            : _test,
                        icon: controller.cloudStorageTesting
                            ? const SizedBox.square(
                                dimension: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Icon(Icons.network_check),
                        label: const Text('测试连接'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: controller.cloudStorageSaving ? null : _save,
                        icon: controller.cloudStorageSaving
                            ? const SizedBox.square(
                                dimension: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Icon(Icons.save_outlined),
                        label: Text(
                          usesBackendDefault
                              ? '使用后端默认'
                              : canSyncCustomStorage
                              ? '保存并同步'
                              : '保存配置',
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _storageField(
    TextEditingController controller,
    String label,
    String hint, {
    TextInputType? keyboardType,
    bool obscureText = false,
    int maxLines = 1,
    String? Function(String?)? validator,
    List<_StorageValueChoice> choices = const [],
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: TextFormField(
        controller: controller,
        keyboardType: keyboardType,
        obscureText: obscureText,
        maxLines: obscureText ? 1 : maxLines,
        validator: validator,
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          suffixIcon: choices.isEmpty
              ? null
              : PopupMenuButton<_StorageValueChoice>(
                  tooltip: '选择常用值',
                  icon: const Icon(Icons.arrow_drop_down),
                  onSelected: (choice) {
                    setState(() => controller.text = choice.value);
                  },
                  itemBuilder: (context) => [
                    for (final choice in choices)
                      PopupMenuItem(value: choice, child: Text(choice.label)),
                  ],
                ),
        ),
      ),
    );
  }

  String? Function(String?) _required(String message) =>
      (value) => value?.trim().isEmpty ?? true ? message : null;

  String? _validateEndpoint(String? value) {
    final text = value?.trim() ?? '';
    final uri = Uri.tryParse(text);
    return uri == null ||
            uri.host.isEmpty ||
            !{'http', 'https'}.contains(uri.scheme)
        ? '请输入有效的 HTTP 或 HTTPS Endpoint'
        : null;
  }

  String? _validatePrefix(String? value) {
    final text = value?.trim() ?? '';
    return text.isEmpty || text.contains('..') || text.contains('\\')
        ? '请输入有效的对象路径'
        : null;
  }

  String? _validateJson(String? value) {
    final text = value?.trim() ?? '';
    if (text.isEmpty) return null;
    try {
      return jsonDecode(text) is Map<String, dynamic> ? null : '必须是 JSON 对象';
    } on FormatException {
      return 'JSON 格式不正确';
    }
  }

  Future<void> _test() async {
    if (!(formKey.currentState?.validate() ?? false)) return;
    setState(() => error = '');
    try {
      await widget.controller.testCloudStorage(_buildConfig());
    } catch (exception) {
      if (mounted) {
        setState(
          () => error = exception.toString().replaceFirst('Exception: ', ''),
        );
      }
    }
  }

  Future<void> _save() async {
    if (!(formKey.currentState?.validate() ?? false)) return;
    setState(() => error = '');
    final config = _buildConfig();
    try {
      await widget.controller.saveCloudStorage(
        config,
        syncRecorder:
            storageMode == 's3' &&
            widget.controller.bleConnected &&
            accessKey.text.trim().isNotEmpty &&
            secretKey.text.trim().isNotEmpty,
      );
    } catch (exception) {
      if (mounted) {
        setState(
          () => error = exception.toString().replaceFirst('Exception: ', ''),
        );
      }
    }
  }

  CloudStorageConfig _buildConfig() {
    final current = widget.controller.cloudStorageConfig;
    if (storageMode == 'backend-default') {
      return current.copyWith(usesBackendDefault: true);
    }
    return current.copyWith(
      type: storageMode,
      provider: storageMode == 's3' ? provider : 'custom',
      endpoint: endpoint.text.trim(),
      region: region.text.trim(),
      bucket: bucket.text.trim(),
      recordingsPrefix: recordingsPrefix.text.trim(),
      notesPrefix: notesPrefix.text.trim(),
      accessKey: accessKey.text.trim(),
      secretKey: secretKey.text.trim(),
      tlsCaCert: tlsCaCert.text.trim(),
      adapterFactory: adapterFactory.text.trim(),
      adapterOptionsJson: adapterOptionsJson.text.trim(),
      usesBackendDefault: false,
    );
  }
}

class _StorageValueChoice {
  const _StorageValueChoice(this.value, this.label);

  final String value;
  final String label;
}

class _AsrServicePage extends StatelessWidget {
  const _AsrServicePage({required this.controller});

  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final asr = controller.backendStatus?.asr;
        final models = asr?.models ?? const <TranscriptionAsrModel>[];
        final customAsrs = controller.customAsrConfigs;
        final scheme = Theme.of(context).colorScheme;

        return Scaffold(
          appBar: AppBar(
            title: const Text('ASR服务'),
            actions: [
              IconButton(
                onPressed: controller.backendLoading
                    ? null
                    : controller.refreshBackendStatus,
                tooltip: '刷新服务状态',
                icon: controller.backendLoading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
              ),
            ],
          ),
          body: RefreshIndicator(
            onRefresh: controller.refreshBackendStatus,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
              children: [
                Card(
                  child: ListTile(
                    leading: Icon(
                      controller.backendReachable
                          ? Icons.cloud_done_outlined
                          : Icons.cloud_off_outlined,
                      color: controller.backendReachable
                          ? scheme.primary
                          : scheme.error,
                    ),
                    title: Text(
                      controller.backendReachable ? '服务已连接' : '服务不可达',
                    ),
                    subtitle: Text(
                      controller.backendStatusMessage.isEmpty
                          ? '下拉刷新服务状态'
                          : controller.backendStatusMessage,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ),
                const SizedBox(height: 18),
                const _SectionTitle(title: '服务选择'),
                const SizedBox(height: 6),
                if (models.isEmpty)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.record_voice_over_outlined),
                      title: const Text('暂无可用 ASR服务'),
                      subtitle: const Text('配置服务后会显示在这里'),
                    ),
                  )
                else
                  for (final model in models) ...[
                    Builder(
                      builder: (context) {
                        final selected = model.id == asr?.selectedModelId;
                        final selectable =
                            model.configured &&
                            model.healthy != false &&
                            controller.backendReachable &&
                            !controller.backendModelSwitching;
                        return Card(
                          color: selected
                              ? scheme.primaryContainer
                              : Theme.of(context).cardTheme.color,
                          child: ListTile(
                            enabled: selectable || selected,
                            leading: Icon(
                              model.healthy == false
                                  ? Icons.error_outline
                                  : model.configured
                                  ? Icons.record_voice_over_outlined
                                  : Icons.settings_outlined,
                              color: model.healthy == false
                                  ? scheme.error
                                  : model.configured
                                  ? scheme.primary
                                  : scheme.outline,
                            ),
                            title: Text(model.name),
                            subtitle: Text(
                              _modelSubtitle(model),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            trailing: Icon(
                              selected
                                  ? Icons.check_circle
                                  : Icons.radio_button_unchecked,
                              color: selected
                                  ? scheme.primary
                                  : scheme.onSurfaceVariant,
                            ),
                            onTap: selectable && !selected
                                ? () => controller.selectBackendModel(model)
                                : null,
                          ),
                        );
                      },
                    ),
                    const SizedBox(height: 8),
                  ],
                const SizedBox(height: 10),
                Row(
                  children: [
                    const Expanded(child: _SectionTitle(title: '自定义服务')),
                    IconButton(
                      onPressed: controller.customAsrSaving
                          ? null
                          : () => _editCustomAsr(context),
                      tooltip: '添加 ASR服务',
                      icon: const Icon(Icons.add),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                if (customAsrs.isEmpty)
                  Card(
                    child: ListTile(
                      leading: Icon(Icons.http_outlined, color: scheme.outline),
                      title: const Text('尚未添加自定义 ASR服务'),
                      subtitle: const Text('点击右上角添加'),
                      onTap: controller.customAsrSaving
                          ? null
                          : () => _editCustomAsr(context),
                    ),
                  )
                else
                  for (final custom in customAsrs) ...[
                    Card(
                      child: ListTile(
                        leading: Icon(
                          Icons.http_outlined,
                          color: custom.enabled
                              ? scheme.primary
                              : scheme.outline,
                        ),
                        title: Text(custom.name),
                        subtitle: Text(
                          custom.enabled
                              ? custom.description
                              : '已停用 · ${custom.description}',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        trailing: PopupMenuButton<String>(
                          tooltip: '管理 ${custom.name}',
                          onSelected: (value) {
                            if (value == 'edit') {
                              _editCustomAsr(context, custom);
                            } else if (value == 'delete') {
                              _deleteCustomAsr(context, custom);
                            }
                          },
                          itemBuilder: (context) => const [
                            PopupMenuItem(value: 'edit', child: Text('编辑')),
                            PopupMenuItem(value: 'delete', child: Text('删除')),
                          ],
                        ),
                        onTap: controller.customAsrSaving
                            ? null
                            : () => _editCustomAsr(context, custom),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
              ],
            ),
          ),
        );
      },
    );
  }

  String _modelSubtitle(TranscriptionAsrModel model) {
    final status = !model.configured
        ? '未配置'
        : model.healthy == false
        ? '服务不可用'
        : model.healthy == true
        ? '在线'
        : '等待检测';
    return model.description.isEmpty
        ? status
        : '$status · ${model.description}';
  }

  Future<void> _editCustomAsr(
    BuildContext context, [
    CustomAsrConfig? config,
  ]) async {
    await showDialog<void>(
      context: context,
      builder: (_) =>
          _CustomAsrDialog(controller: controller, initialConfig: config),
    );
  }

  Future<void> _deleteCustomAsr(
    BuildContext context,
    CustomAsrConfig config,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('删除 ${config.name}？'),
        content: const Text('删除后不能恢复，正在使用时会自动切回默认 ASR服务。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
              foregroundColor: Theme.of(context).colorScheme.onError,
            ),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await controller.deleteCustomAsr(config);
    }
  }
}

class _TranscriptionPanel extends StatelessWidget {
  const _TranscriptionPanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final asr = controller.backendStatus?.asr;
    final available =
        asr?.models.where((model) => model.configured).toList() ?? const [];
    final statusMessage = controller.backendStatusMessage;
    final showStatusMessage =
        statusMessage != '已更新转写状态' && !statusMessage.startsWith('当前使用 ');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.auto_awesome_outlined,
                  color: Theme.of(context).colorScheme.tertiary,
                ),
                const SizedBox(width: 8),
                Text(
                  'ASR服务',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: Theme.of(context).colorScheme.onSurface,
                  ),
                ),
                const Spacer(),
                TextButton(
                  onPressed: () => Navigator.of(context).push<void>(
                    MaterialPageRoute(
                      builder: (context) =>
                          _AsrServicePage(controller: controller),
                    ),
                  ),
                  child: const Text('管理'),
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (available.isNotEmpty)
              DropdownButtonFormField<String>(
                key: ValueKey(asr?.selectedModelId),
                initialValue:
                    available.any((model) => model.id == asr?.selectedModelId)
                    ? asr?.selectedModelId
                    : available.first.id,
                decoration: const InputDecoration(
                  labelText: '语音转文字模型',
                  isDense: true,
                ),
                items: [
                  for (final model in available)
                    DropdownMenuItem(
                      value: model.id,
                      enabled: model.healthy != false,
                      child: Text(
                        model.healthy == false
                            ? '${model.name}（服务不可用）'
                            : model.name,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: (value) {
                  final model = available
                      .where((item) => item.id == value)
                      .firstOrNull;
                  if (model != null) controller.selectBackendModel(model);
                },
              ),
            if (available.isNotEmpty) const SizedBox(height: 8),
            for (final model in available)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(
                  children: [
                    Icon(
                      model.healthy == null
                          ? Icons.help_outline
                          : model.healthy!
                          ? Icons.check_circle_outline
                          : Icons.error_outline,
                      size: 16,
                      color: model.healthy == null
                          ? Theme.of(context).colorScheme.outline
                          : model.healthy!
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.error,
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        model.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                    Text(
                      model.healthy == null
                          ? '检测中'
                          : model.healthy!
                          ? '在线'
                          : '不可用',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: model.healthy == false
                            ? Theme.of(context).colorScheme.error
                            : Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            if (showStatusMessage) ...[
              if (available.isNotEmpty) const SizedBox(height: 6),
              Text(
                statusMessage,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _AsrValueChoice {
  const _AsrValueChoice(this.value, this.label);

  final String value;
  final String label;
}

class _CustomAsrDialog extends StatefulWidget {
  const _CustomAsrDialog({required this.controller, this.initialConfig});
  final RecordingBeanController controller;
  final CustomAsrConfig? initialConfig;

  @override
  State<_CustomAsrDialog> createState() => _CustomAsrDialogState();
}

class _CustomAsrDialogState extends State<_CustomAsrDialog> {
  final formKey = GlobalKey<FormState>();
  late final TextEditingController name;
  late final TextEditingController description;
  late final TextEditingController baseUrl;
  late final TextEditingController healthPath;
  late final TextEditingController transcribePath;
  late final TextEditingController timeoutSeconds;
  late final TextEditingController fileField;
  late final TextEditingController languageField;
  late final TextEditingController promptField;
  late final TextEditingController responseTextPath;
  late final TextEditingController responseLanguagePath;
  late final TextEditingController responseModelPath;
  late final TextEditingController responseSegmentsPath;
  late final TextEditingController apiKey;
  late final TextEditingController apiKeyHeader;
  late final TextEditingController apiKeyPrefix;
  late final TextEditingController headersJson;
  late bool enabled;
  String error = '';

  @override
  void initState() {
    super.initState();
    final config = widget.initialConfig ?? const CustomAsrConfig();
    enabled = config.enabled;
    name = TextEditingController(text: config.name);
    description = TextEditingController(text: config.description);
    baseUrl = TextEditingController(text: config.baseUrl);
    healthPath = TextEditingController(text: config.healthPath);
    transcribePath = TextEditingController(text: config.transcribePath);
    timeoutSeconds = TextEditingController(
      text: config.timeoutSeconds % 1 == 0
          ? config.timeoutSeconds.toInt().toString()
          : config.timeoutSeconds.toString(),
    );
    fileField = TextEditingController(text: config.fileField);
    languageField = TextEditingController(text: config.languageField);
    promptField = TextEditingController(text: config.promptField);
    responseTextPath = TextEditingController(text: config.responseTextPath);
    responseLanguagePath = TextEditingController(
      text: config.responseLanguagePath,
    );
    responseModelPath = TextEditingController(text: config.responseModelPath);
    responseSegmentsPath = TextEditingController(
      text: config.responseSegmentsPath,
    );
    apiKey = TextEditingController();
    apiKeyHeader = TextEditingController(text: config.apiKeyHeader);
    apiKeyPrefix = TextEditingController(text: config.apiKeyPrefix);
    headersJson = TextEditingController(text: config.headersJson);
  }

  @override
  void dispose() {
    for (final controller in [
      name,
      description,
      baseUrl,
      healthPath,
      transcribePath,
      timeoutSeconds,
      fileField,
      languageField,
      promptField,
      responseTextPath,
      responseLanguagePath,
      responseModelPath,
      responseSegmentsPath,
      apiKey,
      apiKeyHeader,
      apiKeyPrefix,
      headersJson,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final config = widget.initialConfig ?? const CustomAsrConfig();
    return AlertDialog(
      title: Text(config.id.isEmpty ? '添加 ASR服务' : '编辑 ASR服务'),
      content: SizedBox(
        width: 520,
        child: Form(
          key: formKey,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('启用此服务'),
                  value: enabled,
                  onChanged: (value) => setState(() => enabled = value),
                ),
                _field(
                  name,
                  '服务名称',
                  '例如：我的 ASR',
                  validator: (value) =>
                      enabled && (value?.trim().isEmpty ?? true)
                      ? '请填写服务名称'
                      : null,
                ),
                _field(
                  description,
                  '服务说明',
                  '例如：局域网语音转写服务',
                  validator: (value) =>
                      (value?.trim().isEmpty ?? true) ? '请填写服务说明' : null,
                ),
                _field(
                  baseUrl,
                  '服务地址',
                  '例如：https://asr.example.com',
                  keyboardType: TextInputType.url,
                  validator: _validateBaseUrl,
                ),
                _field(
                  transcribePath,
                  '转写接口路径',
                  '/transcribe',
                  choices: const [
                    _AsrValueChoice('/transcribe', '/transcribe'),
                    _AsrValueChoice('/v1/transcriptions', '/v1/transcriptions'),
                    _AsrValueChoice('/asr', '/asr'),
                  ],
                  validator: (value) =>
                      enabled && (value?.trim().isEmpty ?? true)
                      ? '请填写转写接口路径'
                      : null,
                ),
                _field(
                  healthPath,
                  '健康检查路径',
                  '/health；不需要可留空',
                  choices: const [
                    _AsrValueChoice('/health', '/health'),
                    _AsrValueChoice('/ready', '/ready'),
                    _AsrValueChoice('/v1/health', '/v1/health'),
                    _AsrValueChoice('', '不检查'),
                  ],
                ),
                _field(
                  timeoutSeconds,
                  '请求超时（秒）',
                  '600',
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  validator: _validateTimeout,
                ),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(top: 4),
                  title: const Text('请求字段'),
                  children: [
                    _field(
                      fileField,
                      '音频字段名',
                      'file',
                      choices: const [
                        _AsrValueChoice('file', 'file'),
                        _AsrValueChoice('audio', 'audio'),
                        _AsrValueChoice('audio_file', 'audio_file'),
                      ],
                    ),
                    _field(
                      languageField,
                      '语言字段名',
                      'language；不需要可留空',
                      choices: const [
                        _AsrValueChoice('language', 'language'),
                        _AsrValueChoice('lang', 'lang'),
                        _AsrValueChoice('locale', 'locale'),
                        _AsrValueChoice('', '不发送'),
                      ],
                    ),
                    _field(
                      promptField,
                      '提示词字段名',
                      'prompt；不需要可留空',
                      choices: const [
                        _AsrValueChoice('prompt', 'prompt'),
                        _AsrValueChoice('context', 'context'),
                        _AsrValueChoice('hotwords', 'hotwords'),
                        _AsrValueChoice('', '不发送'),
                      ],
                    ),
                  ],
                ),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(top: 4),
                  title: const Text('返回字段映射'),
                  children: [
                    _field(
                      responseTextPath,
                      '转写文字字段',
                      '例如：text 或 result.transcript',
                      choices: const [
                        _AsrValueChoice('text', 'text'),
                        _AsrValueChoice('markdown', 'markdown'),
                        _AsrValueChoice('transcript', 'transcript'),
                        _AsrValueChoice('result.text', 'result.text'),
                        _AsrValueChoice(
                          'result.transcript',
                          'result.transcript',
                        ),
                      ],
                      validator: (value) =>
                          enabled && (value?.trim().isEmpty ?? true)
                          ? '请填写转写文字字段'
                          : null,
                    ),
                    _field(
                      responseLanguagePath,
                      '语言字段',
                      'language；不需要可留空',
                      choices: const [
                        _AsrValueChoice('language', 'language'),
                        _AsrValueChoice('lang', 'lang'),
                        _AsrValueChoice('result.language', 'result.language'),
                        _AsrValueChoice('', '不读取'),
                      ],
                    ),
                    _field(
                      responseModelPath,
                      '模型字段',
                      'model；不需要可留空',
                      choices: const [
                        _AsrValueChoice('model', 'model'),
                        _AsrValueChoice('model_name', 'model_name'),
                        _AsrValueChoice('meta.model', 'meta.model'),
                        _AsrValueChoice('', '不读取'),
                      ],
                    ),
                    _field(
                      responseSegmentsPath,
                      '分段字段',
                      'segments；不需要可留空',
                      choices: const [
                        _AsrValueChoice('segments', 'segments'),
                        _AsrValueChoice('chunks', 'chunks'),
                        _AsrValueChoice('result.segments', 'result.segments'),
                        _AsrValueChoice('', '不读取'),
                      ],
                    ),
                  ],
                ),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(top: 4),
                  title: const Text('认证与请求头'),
                  children: [
                    _field(
                      apiKey,
                      'API Key',
                      config.apiKeyConfigured ? '已配置，留空保持原密钥' : '可留空',
                      obscureText: true,
                    ),
                    _field(
                      apiKeyHeader,
                      '认证请求头',
                      'Authorization',
                      choices: const [
                        _AsrValueChoice('Authorization', 'Authorization'),
                        _AsrValueChoice('X-API-Key', 'X-API-Key'),
                        _AsrValueChoice('api-key', 'api-key'),
                      ],
                    ),
                    _field(apiKeyPrefix, '认证前缀', 'Bearer '),
                    _field(
                      headersJson,
                      '自定义请求头（JSON）',
                      '{"X-Account": "example"}',
                      maxLines: 3,
                      validator: _validateHeadersJson,
                    ),
                  ],
                ),
                if (error.isNotEmpty)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      error,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: widget.controller.customAsrSaving
              ? null
              : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton.icon(
          onPressed: widget.controller.customAsrSaving ? null : _save,
          icon: widget.controller.customAsrSaving
              ? const SizedBox.square(
                  dimension: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.check),
          label: const Text('保存并使用'),
        ),
      ],
    );
  }

  Widget _field(
    TextEditingController controller,
    String label,
    String hint, {
    TextInputType? keyboardType,
    bool obscureText = false,
    int maxLines = 1,
    String? Function(String?)? validator,
    List<_AsrValueChoice> choices = const [],
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: TextFormField(
        controller: controller,
        keyboardType: keyboardType,
        obscureText: obscureText,
        maxLines: obscureText ? 1 : maxLines,
        validator: validator,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          suffixIcon: choices.isEmpty
              ? null
              : PopupMenuButton<_AsrValueChoice>(
                  tooltip: '选择常用值',
                  icon: const Icon(Icons.arrow_drop_down),
                  onSelected: (choice) {
                    setState(() => controller.text = choice.value);
                  },
                  itemBuilder: (context) => [
                    for (final choice in choices)
                      PopupMenuItem(value: choice, child: Text(choice.label)),
                  ],
                ),
        ),
      ),
    );
  }

  String? _validateBaseUrl(String? value) {
    if (!enabled) return null;
    final uri = Uri.tryParse(value?.trim() ?? '');
    if (uri == null ||
        uri.host.isEmpty ||
        !{'http', 'https'}.contains(uri.scheme)) {
      return '请输入有效的 HTTP 或 HTTPS 地址';
    }
    return null;
  }

  String? _validateTimeout(String? value) {
    final seconds = double.tryParse(value?.trim() ?? '');
    if (!enabled && (value?.trim().isEmpty ?? true)) return null;
    return seconds == null || !seconds.isFinite || seconds <= 0
        ? '请输入大于 0 的秒数'
        : null;
  }

  String? _validateHeadersJson(String? value) {
    final text = value?.trim() ?? '';
    if (text.isEmpty) return null;
    try {
      if (jsonDecode(text) is! Map<String, dynamic>) {
        return '请求头必须是 JSON 对象';
      }
    } on FormatException {
      return '请求头 JSON 格式不正确';
    }
    return null;
  }

  Future<void> _save() async {
    if (!(formKey.currentState?.validate() ?? false)) return;
    final current = widget.controller.customAsrConfig;
    final next = current.copyWith(
      enabled: enabled,
      name: name.text.trim(),
      description: description.text.trim(),
      baseUrl: baseUrl.text.trim(),
      healthPath: healthPath.text.trim(),
      transcribePath: transcribePath.text.trim(),
      timeoutSeconds:
          double.tryParse(timeoutSeconds.text.trim()) ?? current.timeoutSeconds,
      fileField: fileField.text.trim(),
      languageField: languageField.text.trim(),
      promptField: promptField.text.trim(),
      responseTextPath: responseTextPath.text.trim(),
      responseLanguagePath: responseLanguagePath.text.trim(),
      responseModelPath: responseModelPath.text.trim(),
      responseSegmentsPath: responseSegmentsPath.text.trim(),
      apiKey: apiKey.text,
      apiKeyHeader: apiKeyHeader.text.trim(),
      apiKeyPrefix: apiKeyPrefix.text,
      headersJson: headersJson.text.trim(),
    );
    try {
      await widget.controller.saveCustomAsr(next);
      if (mounted) Navigator.of(context).pop();
    } catch (exception) {
      if (mounted) {
        setState(
          () => error = exception.toString().replaceFirst('Exception: ', ''),
        );
      }
    }
  }
}

class _FilesPage extends StatefulWidget {
  const _FilesPage({required this.controller});
  final RecordingBeanController controller;

  @override
  State<_FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends State<_FilesPage> {
  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return SafeArea(
      top: false,
      child: RefreshIndicator(
        onRefresh: controller.refreshFiles,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
          children: [
            if (controller.downloadingFileName.isNotEmpty) ...[
              _DownloadProgressPanel(controller: controller),
              const SizedBox(height: 10),
            ],
            if (!controller.bleConnected && !controller.connected) ...[
              _FilesDevicePrompt(controller: controller),
              const SizedBox(height: 10),
            ],
            if (controller.bleConnected || controller.connected)
              Row(
                children: [
                  Expanded(
                    child: Text(
                      controller.message,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                  IconButton(
                    onPressed: controller.loading
                        ? null
                        : controller.refreshFiles,
                    tooltip: '刷新录音文件',
                    icon: controller.loading
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.refresh),
                  ),
                ],
              ),
            _SelectionBar(
              controller: controller,
              confirmDelete: () => _confirmBatchDelete(context),
            ),
            const SizedBox(height: 8),
            if (controller.files.isEmpty)
              const _EmptyFiles()
            else
              for (final file in controller.files) ...[
                _FileTile(
                  controller: controller,
                  file: file,
                  confirmDelete: () => _confirmDelete(context, file),
                ),
                const SizedBox(height: 8),
              ],
          ],
        ),
      ),
    );
  }

  Future<void> _confirmDelete(BuildContext context, RecordingFile file) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除录音？'),
        content: Text('将永久删除设备 TF 卡中的 ${file.name}，该操作无法撤销。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
              foregroundColor: Theme.of(context).colorScheme.onError,
            ),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed == true) await widget.controller.deleteFiles([file]);
  }

  Future<void> _confirmBatchDelete(BuildContext context) async {
    final count = widget.controller.selectedFileNames.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('批量删除录音？'),
        content: Text('将永久删除设备 TF 卡中选中的 $count 个录音，该操作无法撤销。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
              foregroundColor: Theme.of(context).colorScheme.onError,
            ),
            child: Text('删除 $count 个'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await widget.controller.deleteFiles(
        widget.controller.files.where(
          (file) => widget.controller.selectedFileNames.contains(file.name),
        ),
      );
    }
  }
}

class _FilesDevicePrompt extends StatelessWidget {
  const _FilesDevicePrompt({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Row(
        children: [
          const Padding(
            padding: EdgeInsets.all(12),
            child: Icon(Icons.bluetooth_searching),
          ),
          Expanded(
            child: Text(
              '连接录音豆后查看录音',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ),
          TextButton(
            onPressed: () => controller.setPage(0),
            child: const Text('去连接'),
          ),
          const SizedBox(width: 4),
        ],
      ),
    );
  }
}

class _SelectionBar extends StatelessWidget {
  const _SelectionBar({required this.controller, required this.confirmDelete});
  final RecordingBeanController controller;
  final VoidCallback confirmDelete;

  @override
  Widget build(BuildContext context) {
    if (!controller.selectionMode) {
      return Row(
        children: [
          Text(
            '${controller.files.length} 个录音',
            style: Theme.of(context).textTheme.titleSmall,
          ),
          const Spacer(),
          TextButton.icon(
            onPressed: controller.files.isEmpty
                ? null
                : controller.enterSelectionMode,
            icon: const Icon(Icons.checklist, size: 18),
            label: const Text('选择'),
          ),
        ],
      );
    }
    return Wrap(
      spacing: 4,
      runSpacing: 4,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        IconButton(
          onPressed: controller.exitSelectionMode,
          tooltip: '退出选择',
          icon: const Icon(Icons.close),
        ),
        Text('已选 ${controller.selectedFileNames.length} 个'),
        TextButton(onPressed: controller.selectAll, child: const Text('全选')),
        TextButton(
          onPressed: controller.selectCleanupCandidates,
          child: const Text('选中空录音'),
        ),
        FilledButton.icon(
          onPressed: controller.selectedFileNames.isEmpty
              ? null
              : confirmDelete,
          style: FilledButton.styleFrom(
            backgroundColor: Theme.of(context).colorScheme.error,
            foregroundColor: Theme.of(context).colorScheme.onError,
          ),
          icon: const Icon(Icons.delete_outline, size: 18),
          label: const Text('删除'),
        ),
      ],
    );
  }
}

class _FileTile extends StatelessWidget {
  const _FileTile({
    required this.controller,
    required this.file,
    required this.confirmDelete,
  });
  final RecordingBeanController controller;
  final RecordingFile file;
  final VoidCallback confirmDelete;

  @override
  Widget build(BuildContext context) {
    final selected = controller.selectedFileNames.contains(file.name);
    final deleting = controller.pendingDeletionNames.contains(file.name);
    return Card(
      color: selected
          ? Theme.of(context).colorScheme.primaryContainer
          : Theme.of(context).cardTheme.color,
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onLongPress: controller.selectionMode || deleting
            ? null
            : () {
                controller.enterSelectionMode();
                controller.toggleSelection(file);
              },
        onTap: controller.selectionMode
            ? () => controller.toggleSelection(file)
            : deleting
            ? null
            : () => controller.downloadFile(file),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
          child: Row(
            children: [
              if (controller.selectionMode) ...[
                Checkbox(
                  value: selected,
                  onChanged: (_) => controller.toggleSelection(file),
                ),
                const SizedBox(width: 4),
              ] else ...[
                Icon(
                  Icons.audio_file_outlined,
                  color: Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(width: 10),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      file.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontWeight: FontWeight.w600,
                        color: Theme.of(context).colorScheme.onSurface,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${_formatDuration(file.durationSeconds)} · ${_formatBytes(file.size)} · ${file.createdAt}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              if (!controller.selectionMode) ...[
                IconButton(
                  onPressed: controller.downloadingFileName.isEmpty && !deleting
                      ? () => controller.downloadFile(file)
                      : null,
                  tooltip: '下载并打开录音',
                  icon: controller.downloadingFileName == file.name
                      ? _DownloadIconProgress(controller: controller)
                      : const Icon(Icons.download_outlined),
                ),
                IconButton(
                  onPressed: deleting ? null : confirmDelete,
                  tooltip: '删除录音',
                  icon: deleting
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Icon(
                          Icons.delete_outline,
                          color: Theme.of(context).colorScheme.error,
                        ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _DownloadProgressPanel extends StatelessWidget {
  const _DownloadProgressPanel({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final total = controller.downloadTotalBytes;
    final hasTotal = total > 0;
    final progress = hasTotal
        ? (controller.downloadReceivedBytes / total).clamp(0.0, 1.0)
        : null;
    final detail = controller.downloadWaitingForWifi
        ? '正在等待设备 Wi-Fi'
        : hasTotal
        ? '${_formatBytes(controller.downloadReceivedBytes)} / ${_formatBytes(total)}'
        : '已下载 ${_formatBytes(controller.downloadReceivedBytes)}';
    final percent = progress == null ? '' : ' ${(progress * 100).round()}%';
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 11, 12, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.downloading_outlined, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '下载中：${controller.downloadingFileName}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 9),
            LinearProgressIndicator(value: progress),
            const SizedBox(height: 7),
            Text(
              '$detail$percent',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DownloadIconProgress extends StatelessWidget {
  const _DownloadIconProgress({required this.controller});
  final RecordingBeanController controller;

  @override
  Widget build(BuildContext context) {
    final total = controller.downloadTotalBytes;
    final value = total > 0
        ? (controller.downloadReceivedBytes / total).clamp(0.0, 1.0)
        : null;
    return SizedBox.square(
      dimension: 21,
      child: CircularProgressIndicator(value: value, strokeWidth: 2),
    );
  }
}

class _EmptyFiles extends StatelessWidget {
  const _EmptyFiles();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 72),
      child: Column(
        children: [
          Icon(
            Icons.folder_open,
            size: 44,
            color: Theme.of(context).colorScheme.outline,
          ),
          const SizedBox(height: 10),
          const Text('暂无录音文件'),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 76,
            child: Text(
              label,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              textAlign: TextAlign.end,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusDot extends StatelessWidget {
  const _StatusDot({required this.active});
  final bool active;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 9,
      height: 9,
      decoration: BoxDecoration(
        color: active
            ? Theme.of(context).colorScheme.primary
            : Theme.of(context).colorScheme.outline,
        shape: BoxShape.circle,
      ),
    );
  }
}

String _formatDuration(int seconds) {
  final safe = seconds < 0 ? 0 : seconds;
  final hours = safe ~/ 3600;
  final minutes = (safe % 3600) ~/ 60;
  final remaining = safe % 60;
  if (hours > 0) {
    return '${hours.toString().padLeft(2, '0')}:${minutes.toString().padLeft(2, '0')}:${remaining.toString().padLeft(2, '0')}';
  }
  return '${minutes.toString().padLeft(2, '0')}:${remaining.toString().padLeft(2, '0')}';
}

String _formatBytes(int bytes) {
  if (bytes >= 1024 * 1024 * 1024) {
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  if (bytes >= 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
  return '$bytes B';
}
