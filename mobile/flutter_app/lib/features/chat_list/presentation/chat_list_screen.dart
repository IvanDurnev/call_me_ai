import 'package:flutter/material.dart';

import '../../../core/models/chat_models.dart';
import '../../../core/network/api_client.dart';
import '../../../core/session/app_scope.dart';
import '../../auth/presentation/auth_screen.dart';
import '../../chat/presentation/chat_screen.dart';

class ChatListScreen extends StatefulWidget {
  const ChatListScreen({super.key});

  static const routeName = '/chats';

  @override
  State<ChatListScreen> createState() => _ChatListScreenState();
}

class _ChatListScreenState extends State<ChatListScreen> {
  bool _isLoading = true;
  bool _didScheduleInitialLoad = false;
  String? _errorText;
  List<ChatListItem> _items = const [];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_didScheduleInitialLoad) {
      return;
    }
    _didScheduleInitialLoad = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _loadChats();
      }
    });
  }

  Future<void> _loadChats() async {
    final scope = AppScope.of(context);
    setState(() {
      _isLoading = true;
      _errorText = null;
    });
    try {
      final payload = await scope.getAuthorizedJson('/api/mobile/chats');
      final rawItems = payload['items'] as List<dynamic>? ?? const [];
      setState(() {
        _items = rawItems
            .whereType<Map<String, dynamic>>()
            .map(ChatListItem.fromJson)
            .toList(growable: false);
      });
    } on ApiException catch (error) {
      if (error.statusCode == 401 && mounted) {
        Navigator.of(context).pushNamedAndRemoveUntil(AuthScreen.routeName, (_) => false);
        return;
      }
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
    final scope = AppScope.of(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Чаты'),
        actions: [
          IconButton(
            onPressed: () async {
              await scope.session.logout(scope.apiClient);
              if (!context.mounted) return;
              Navigator.of(context).pushNamedAndRemoveUntil(AuthScreen.routeName, (_) => false);
            },
            icon: const Icon(Icons.logout),
          ),
          IconButton(
            onPressed: _isLoading ? null : _loadChats,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Builder(
        builder: (context) {
          if (_isLoading) {
            return const Center(child: CircularProgressIndicator());
          }
          if (_errorText != null) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(_errorText!, textAlign: TextAlign.center),
              ),
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(16),
            itemBuilder: (context, index) {
              final item = _items[index];
              return ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                tileColor: Colors.white,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
                leading: CircleAvatar(
                  radius: 28,
                  backgroundColor: const Color(0xFFEAF4FF),
                  backgroundImage: item.avatarUrl != null && item.avatarUrl!.isNotEmpty
                      ? NetworkImage(item.avatarUrl!)
                      : null,
                  child: item.avatarUrl == null || item.avatarUrl!.isEmpty
                      ? Text(
                          item.title.isEmpty ? '?' : item.title.substring(0, 1),
                          style: const TextStyle(
                            color: Color(0xFF2D3557),
                            fontWeight: FontWeight.w700,
                          ),
                        )
                      : null,
                ),
                title: Text(item.title),
                onTap: () {
                  Navigator.of(context).pushNamed(
                    ChatScreen.routeName,
                    arguments: ChatScreenArgs(
                      characterSlug: item.characterSlug,
                      title: item.title,
                      avatarUrl: item.avatarUrl,
                    ),
                  );
                },
              );
            },
            separatorBuilder: (_, __) => const SizedBox(height: 12),
            itemCount: _items.length,
          );
        },
      ),
    );
  }
}
