import 'package:flutter/material.dart';

import '../core/config/app_config.dart';
import '../core/network/api_client.dart';
import '../core/session/app_scope.dart';
import '../core/session/session_controller.dart';
import '../core/session/session_storage.dart';
import '../features/auth/presentation/auth_screen.dart';
import '../features/chat/presentation/chat_screen.dart';
import '../features/chat_list/presentation/chat_list_screen.dart';

class CallMeAiApp extends StatefulWidget {
  const CallMeAiApp({super.key});

  @override
  State<CallMeAiApp> createState() => _CallMeAiAppState();
}

class _CallMeAiAppState extends State<CallMeAiApp> {
  static final SessionStorage _sessionStorage = SessionStorage();
  static final SessionController _sessionController = SessionController(storage: _sessionStorage);
  static final ApiClient _apiClient = ApiClient(baseUrl: AppConfig.apiBaseUrl);
  late final Future<void> _bootstrapFuture;

  @override
  void initState() {
    super.initState();
    _bootstrapFuture = _sessionController.initialize(_apiClient);
  }

  @override
  Widget build(BuildContext context) {
    return AppScope(
      apiClient: _apiClient,
      sessionController: _sessionController,
      child: AnimatedBuilder(
        animation: _sessionController,
        builder: (context, _) {
          return FutureBuilder<void>(
            future: _bootstrapFuture,
            builder: (context, snapshot) {
              return MaterialApp(
                title: 'Call Me AI',
                theme: ThemeData(
                  colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF0C7A6A)),
                  scaffoldBackgroundColor: const Color(0xFFF5F1E8),
                  useMaterial3: true,
                ),
                home: !_sessionController.isReady
                    ? const _BootstrapScreen()
                    : _sessionController.isAuthenticated
                        ? const ChatListScreen()
                        : const AuthScreen(),
                routes: {
                  AuthScreen.routeName: (_) => const AuthScreen(),
                  ChatListScreen.routeName: (_) => const ChatListScreen(),
                },
                onGenerateRoute: (settings) {
                  if (settings.name == ChatScreen.routeName && settings.arguments is ChatScreenArgs) {
                    final args = settings.arguments as ChatScreenArgs;
                    return MaterialPageRoute<void>(
                      builder: (_) => ChatScreen(args: args),
                      settings: settings,
                    );
                  }
                  return null;
                },
              );
            },
          );
        },
      ),
    );
  }
}

class _BootstrapScreen extends StatelessWidget {
  const _BootstrapScreen();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: CircularProgressIndicator(),
      ),
    );
  }
}
