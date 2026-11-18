import 'dart:convert';
import 'dart:io';

class AuthApi {
  AuthApi({HttpClient? client}) : _client = client ?? HttpClient();

  final HttpClient _client;

  Uri normalizeAddress(String value) {
    var candidate = value.trim();
    if (!candidate.startsWith('http://') && !candidate.startsWith('https://')) {
      candidate = 'http://$candidate';
    }
    final uri = Uri.tryParse(candidate);
    if (uri == null ||
        uri.host.isEmpty ||
        !{'http', 'https'}.contains(uri.scheme)) {
      throw const AuthException('状态服务地址不正确。');
    }
    return uri.replace(path: uri.path == '/' ? '' : uri.path);
  }

  Future<AuthSession> login(Uri base, String email, String password) async {
    return _sessionRequest(base, '/api/auth/login', {
      'email': email,
      'password': password,
    });
  }

  Future<AuthSession> register(Uri base, String email, String password) async {
    return _sessionRequest(base, '/api/auth/register', {
      'email': email,
      'password': password,
    });
  }

  Future<AuthUser> me(Uri base, String token) async {
    final response = await _request(base, 'GET', '/api/auth/me', token: token);
    final user = response['user'];
    if (user is! Map<String, dynamic>) {
      throw const AuthException('账户信息格式不正确。');
    }
    return AuthUser.fromJson(user);
  }

  Future<void> logout(Uri base, String token) async {
    await _request(base, 'POST', '/api/auth/logout', token: token);
  }

  Future<void> changePassword(
    Uri base,
    String token,
    String currentPassword,
    String newPassword,
  ) async {
    await _request(
      base,
      'POST',
      '/api/auth/change-password',
      token: token,
      body: {'current_password': currentPassword, 'new_password': newPassword},
    );
  }

  Future<AuthSession> _sessionRequest(
    Uri base,
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await _request(base, 'POST', path, body: body);
    final token = response['token'];
    final user = response['user'];
    if (token is! String || token.isEmpty || user is! Map<String, dynamic>) {
      throw const AuthException('登录服务返回的数据不完整。');
    }
    return AuthSession(token: token, user: AuthUser.fromJson(user));
  }

  Future<Map<String, dynamic>> _request(
    Uri base,
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? token,
  }) async {
    final request = await _client.openUrl(method, _resolve(base, path));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (token != null && token.isNotEmpty) {
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    }
    if (body != null) {
      final encoded = utf8.encode(jsonEncode(body));
      request.headers.contentType = ContentType.json;
      request.contentLength = encoded.length;
      request.add(encoded);
    }
    final response = await request.close().timeout(const Duration(seconds: 12));
    final text = await utf8.decoder.bind(response).join();
    Map<String, dynamic> decoded = const {};
    if (text.isNotEmpty) {
      final json = jsonDecode(text);
      if (json is! Map<String, dynamic>) {
        throw const AuthException('认证服务返回的数据格式不正确。');
      }
      decoded = json;
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuthException(
        decoded['error'] as String? ?? '认证服务暂时不可用。',
        statusCode: response.statusCode,
      );
    }
    return decoded;
  }

  Uri _resolve(Uri base, String path) => base.replace(path: path, query: null);

  void close() => _client.close(force: true);
}

class AuthUser {
  const AuthUser({
    required this.id,
    required this.email,
    required this.createdAt,
  });

  factory AuthUser.fromJson(Map<String, dynamic> json) => AuthUser(
    id: json['id'] as int? ?? 0,
    email: json['email'] as String? ?? '',
    createdAt: json['created_at'] as String? ?? '',
  );

  final int id;
  final String email;
  final String createdAt;
}

class AuthSession {
  const AuthSession({required this.token, required this.user});

  final String token;
  final AuthUser user;
}

class AuthException implements Exception {
  const AuthException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}
