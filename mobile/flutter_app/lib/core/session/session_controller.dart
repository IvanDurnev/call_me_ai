import 'package:flutter/foundation.dart';

import '../models/mobile_user.dart';
import '../network/api_client.dart';
import 'session_storage.dart';

class SessionController extends ChangeNotifier {
  SessionController({
    required SessionStorage storage,
  }) : _storage = storage;

  final SessionStorage _storage;
  String? _accessToken;
  String? _refreshToken;
  MobileUser? _user;
  bool _isReady = false;

  String? get accessToken => _accessToken;
  String? get refreshToken => _refreshToken;
  MobileUser? get user => _user;
  bool get isReady => _isReady;
  bool get isAuthenticated => _accessToken != null && _user != null;

  Future<void> initialize(ApiClient apiClient) async {
    final stored = await _storage.load();
    if (stored == null) {
      _isReady = true;
      notifyListeners();
      return;
    }

    _accessToken = stored.accessToken;
    _refreshToken = stored.refreshToken;
    _user = stored.user;

    try {
      final refreshed = await apiClient.postJson(
        '/api/mobile/auth/refresh',
        body: {'refresh_token': stored.refreshToken},
      );
      _accessToken = refreshed['access_token'] as String? ?? stored.accessToken;

      final me = await apiClient.getJson(
        '/api/mobile/me',
        accessToken: _accessToken,
      );
      _user = MobileUser.fromJson(me['user'] as Map<String, dynamic>? ?? const {});
      await _storage.save(
        accessToken: _accessToken ?? '',
        refreshToken: _refreshToken ?? '',
        user: _user!,
      );
    } on ApiException {
      await _storage.clear();
      _accessToken = null;
      _refreshToken = null;
      _user = null;
    }

    _isReady = true;
    notifyListeners();
  }

  Future<void> setSession({
    required String accessToken,
    required String refreshToken,
    required MobileUser user,
  }) async {
    _accessToken = accessToken;
    _refreshToken = refreshToken;
    _user = user;
    await _storage.save(
      accessToken: accessToken,
      refreshToken: refreshToken,
      user: user,
    );
    notifyListeners();
  }

  Future<void> refreshAccessToken(ApiClient apiClient) async {
    if (_refreshToken == null || _refreshToken!.isEmpty) {
      throw const ApiException('Refresh token is missing.');
    }
    final payload = await apiClient.postJson(
      '/api/mobile/auth/refresh',
      body: {'refresh_token': _refreshToken},
    );
    _accessToken = payload['access_token'] as String? ?? _accessToken;
    if (_user != null && _accessToken != null) {
      await _storage.save(
        accessToken: _accessToken!,
        refreshToken: _refreshToken!,
        user: _user!,
      );
    }
    notifyListeners();
  }

  Future<void> logout(ApiClient apiClient) async {
    final token = _refreshToken;
    if (token != null && token.isNotEmpty) {
      try {
        await apiClient.postJson(
          '/api/mobile/auth/logout',
          body: {'refresh_token': token},
        );
      } on ApiException {
        // Ignore remote logout failures and still clear local session.
      }
    }
    await clear();
  }

  Future<void> clear() async {
    _accessToken = null;
    _refreshToken = null;
    _user = null;
    await _storage.clear();
    notifyListeners();
  }
}
