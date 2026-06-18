import 'package:flutter/material.dart';
import 'dart:ui';

import '../../../core/models/mobile_user.dart';
import '../../../core/network/api_client.dart';
import '../../../core/session/app_scope.dart';
import '../../chat_list/presentation/chat_list_screen.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  static const routeName = '/auth';

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final TextEditingController _loginController = TextEditingController();
  final TextEditingController _codeController = TextEditingController();
  bool _codeRequested = false;
  bool _isLoading = false;
  String? _errorText;
  String? _infoText;

  @override
  void dispose() {
    _loginController.dispose();
    _codeController.dispose();
    super.dispose();
  }

  Future<void> _requestCode() async {
    setState(() {
      _isLoading = true;
      _errorText = null;
      _infoText = null;
    });

    final scope = AppScope.of(context);
    try {
      final payload = await scope.apiClient.postJson(
        '/api/mobile/auth/request-code',
        body: {'login': _loginController.text.trim()},
      );
      setState(() {
        _codeRequested = true;
        _infoText = 'Код отправлен на ${payload['masked_destination'] ?? 'указанный адрес'}.';
      });
    } on ApiException catch (error) {
      setState(() {
        _errorText = error.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  Future<void> _verifyCode() async {
    setState(() {
      _isLoading = true;
      _errorText = null;
    });

    final scope = AppScope.of(context);
    try {
      final payload = await scope.apiClient.postJson(
        '/api/mobile/auth/verify-code',
        body: {
          'login': _loginController.text.trim(),
          'code': _codeController.text.trim(),
          'purpose': 'mobile_login',
          'device_name': 'Flutter app',
        },
      );
      final user = MobileUser.fromJson(payload['user'] as Map<String, dynamic>? ?? const {});
      await scope.session.setSession(
        accessToken: payload['access_token'] as String? ?? '',
        refreshToken: payload['refresh_token'] as String? ?? '',
        user: user,
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacementNamed(ChatListScreen.routeName);
    } on ApiException catch (error) {
      setState(() {
        _errorText = error.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Stack(
        fit: StackFit.expand,
        children: [
          ImageFiltered(
            imageFilter: ImageFilter.blur(sigmaX: 0.35, sigmaY: 0.35),
            child: Transform.scale(
              scale: 1.06,
              child: Image.asset(
                'assets/images/room.webp',
                fit: BoxFit.cover,
              ),
            ),
          ),
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  const Color(0xFFFFF3E8).withValues(alpha: 0.66),
                  const Color(0xFFEAF4FF).withValues(alpha: 0.72),
                ],
              ),
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 520),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(32),
                    child: BackdropFilter(
                      filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                      child: Container(
                        padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.46),
                          borderRadius: BorderRadius.circular(32),
                          border: Border.all(
                            color: Colors.white.withValues(alpha: 0.58),
                          ),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x2E37415B),
                              blurRadius: 40,
                              offset: Offset(0, 20),
                            ),
                          ],
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Center(
                              child: Image.asset(
                                'assets/images/logo.webp',
                                width: 164,
                                fit: BoxFit.contain,
                              ),
                            ),
                            const SizedBox(height: 24),
                            Text(
                              'Волшебный мессенджер',
                              style: theme.textTheme.headlineMedium?.copyWith(
                                fontWeight: FontWeight.w800,
                                color: const Color(0xFF2D3557),
                              ),
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 10),
                            Text(
                              'Введите адрес электронной почты, чтобы войти',
                              style: theme.textTheme.bodyLarge?.copyWith(
                                height: 1.5,
                                color: const Color(0xFF6F7897),
                              ),
                            ),
                            const SizedBox(height: 24),
                            TextField(
                              controller: _loginController,
                              keyboardType: TextInputType.emailAddress,
                              decoration: _fieldDecoration(
                                label: 'Адрес электронной почты',
                              ),
                            ),
                            const SizedBox(height: 16),
                            if (_codeRequested) ...[
                              TextField(
                                controller: _codeController,
                                keyboardType: TextInputType.number,
                                decoration: _fieldDecoration(
                                  label: 'Код подтверждения',
                                ),
                              ),
                              const SizedBox(height: 16),
                            ],
                            SizedBox(
                              width: double.infinity,
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(999),
                                  boxShadow: const [
                                    BoxShadow(
                                      color: Color(0x47FF7E80),
                                      blurRadius: 28,
                                      offset: Offset(0, 14),
                                    ),
                                  ],
                                  gradient: const LinearGradient(
                                    colors: [
                                      Color(0xFFFF8F70),
                                      Color(0xFFFF6D8D),
                                    ],
                                  ),
                                ),
                                child: FilledButton(
                                  onPressed: _isLoading ? null : (_codeRequested ? _verifyCode : _requestCode),
                                  style: FilledButton.styleFrom(
                                    backgroundColor: Colors.transparent,
                                    shadowColor: Colors.transparent,
                                    foregroundColor: Colors.white,
                                    padding: const EdgeInsets.symmetric(vertical: 18),
                                  ),
                                  child: Text(
                                    _isLoading ? 'Подождите...' : (_codeRequested ? 'Войти' : 'Получить код'),
                                    style: const TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                                ),
                              ),
                            ),
                            if (_infoText != null) ...[
                              const SizedBox(height: 16),
                              Text(
                                _infoText!,
                                style: theme.textTheme.bodyMedium?.copyWith(
                                  color: const Color(0xFF2D3557),
                                ),
                              ),
                            ],
                            if (_errorText != null) ...[
                              const SizedBox(height: 16),
                              Text(
                                _errorText!,
                                style: theme.textTheme.bodyMedium?.copyWith(
                                  color: theme.colorScheme.error,
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  InputDecoration _fieldDecoration({
    required String label,
  }) {
    const borderColor = Color(0x337BA4D1);
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Color(0xFF6F7897)),
      filled: true,
      fillColor: Colors.white.withValues(alpha: 0.82),
      contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 18),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: const BorderSide(color: borderColor),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: const BorderSide(color: borderColor),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(20),
        borderSide: const BorderSide(
          color: Color(0xFFFF8F70),
          width: 1.4,
        ),
      ),
    );
  }
}
