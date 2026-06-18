import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../models/mobile_user.dart';

class StoredSession {
  const StoredSession({
    required this.accessToken,
    required this.refreshToken,
    required this.user,
  });

  final String accessToken;
  final String refreshToken;
  final MobileUser user;
}

class SessionStorage {
  SessionStorage({
    FlutterSecureStorage? secureStorage,
  }) : _secureStorage = secureStorage ?? const FlutterSecureStorage();

  static const _accessTokenKey = 'access_token';
  static const _refreshTokenKey = 'refresh_token';
  static const _userKey = 'mobile_user';

  final FlutterSecureStorage _secureStorage;

  Future<void> save({
    required String accessToken,
    required String refreshToken,
    required MobileUser user,
  }) async {
    await _secureStorage.write(key: _accessTokenKey, value: accessToken);
    await _secureStorage.write(key: _refreshTokenKey, value: refreshToken);
    await _secureStorage.write(
      key: _userKey,
      value: jsonEncode({
        'id': user.id,
        'uuid': user.uuid,
        'name': user.name,
        'email': user.email,
        'phone': user.phone,
        'email_verified': user.emailVerified,
        'has_call_access': user.hasCallAccess,
        'remaining_trial_minutes': user.remainingTrialMinutes,
      }),
    );
  }

  Future<StoredSession?> load() async {
    final accessToken = await _secureStorage.read(key: _accessTokenKey);
    final refreshToken = await _secureStorage.read(key: _refreshTokenKey);
    final userJson = await _secureStorage.read(key: _userKey);
    if (accessToken == null || refreshToken == null || userJson == null) {
      return null;
    }

    final decoded = jsonDecode(userJson) as Map<String, dynamic>;
    return StoredSession(
      accessToken: accessToken,
      refreshToken: refreshToken,
      user: MobileUser.fromJson(decoded),
    );
  }

  Future<void> clear() async {
    await _secureStorage.delete(key: _accessTokenKey);
    await _secureStorage.delete(key: _refreshTokenKey);
    await _secureStorage.delete(key: _userKey);
  }
}
