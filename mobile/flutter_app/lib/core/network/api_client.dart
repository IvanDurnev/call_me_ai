import 'dart:async';
import 'dart:convert';
import 'dart:io';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({required this.baseUrl});

  static const _requestTimeout = Duration(seconds: 70);

  final String baseUrl;

  Future<Map<String, dynamic>> getJson(
    String path, {
    String? accessToken,
  }) {
    return _send('GET', path, accessToken: accessToken);
  }

  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
    String? accessToken,
  }) {
    return _send('POST', path, body: body, accessToken: accessToken);
  }

  Future<Map<String, dynamic>> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? accessToken,
  }) async {
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 15);
    try {
      final uri = Uri.parse('$baseUrl$path');
      final request = await client.openUrl(method, uri);
      request.headers.set(
        HttpHeaders.contentTypeHeader,
        'application/json; charset=utf-8',
      );
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (accessToken != null && accessToken.isNotEmpty) {
        request.headers
            .set(HttpHeaders.authorizationHeader, 'Bearer $accessToken');
      }
      if (body != null) {
        final encodedBody = utf8.encode(jsonEncode(body));
        request.contentLength = encodedBody.length;
        request.add(encodedBody);
      }

      final response = await request.close().timeout(_requestTimeout);
      final responseBody =
          await utf8.decodeStream(response).timeout(_requestTimeout);
      final json = responseBody.isEmpty
          ? <String, dynamic>{}
          : jsonDecode(responseBody) as Map<String, dynamic>;
      if (response.statusCode >= 400) {
        throw ApiException(
          json['error'] as String? ?? 'Request failed.',
          statusCode: response.statusCode,
        );
      }
      if (json['ok'] == false) {
        throw ApiException(json['error'] as String? ?? 'Request failed.');
      }
      return json;
    } on SocketException {
      throw const ApiException('Не удалось подключиться к серверу.');
    } on TimeoutException {
      throw const ApiException('Сервер слишком долго не отвечает.');
    } on FormatException {
      throw const ApiException('Сервер вернул некорректный ответ.');
    } finally {
      client.close(force: true);
    }
  }
}
