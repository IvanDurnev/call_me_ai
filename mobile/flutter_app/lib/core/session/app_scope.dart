import 'package:flutter/widgets.dart';

import '../network/api_client.dart';
import 'session_controller.dart';

class AppScope extends InheritedNotifier<SessionController> {
  const AppScope({
    super.key,
    required this.apiClient,
    required SessionController sessionController,
    required super.child,
  }) : super(notifier: sessionController);

  final ApiClient apiClient;

  static AppScope of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'AppScope is missing in widget tree.');
    return scope!;
  }

  SessionController get session => notifier!;

  Future<Map<String, dynamic>> getAuthorizedJson(String path) async {
    return _performAuthorizedRequest(
      () => apiClient.getJson(path, accessToken: session.accessToken),
    );
  }

  Future<Map<String, dynamic>> postAuthorizedJson(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    return _performAuthorizedRequest(
      () => apiClient.postJson(path, body: body, accessToken: session.accessToken),
    );
  }

  Future<Map<String, dynamic>> _performAuthorizedRequest(
    Future<Map<String, dynamic>> Function() request,
  ) async {
    try {
      return await request();
    } on ApiException catch (error) {
      if (error.statusCode != 401) {
        rethrow;
      }
    }

    try {
      await session.refreshAccessToken(apiClient);
      return await request();
    } on ApiException catch (error) {
      if (error.statusCode == 401) {
        await session.clear();
      }
      rethrow;
    }
  }
}
