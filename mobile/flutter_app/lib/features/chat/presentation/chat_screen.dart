import 'package:flutter/material.dart';

import '../../../core/models/chat_models.dart';
import '../../../core/network/api_client.dart';
import '../../../core/session/app_scope.dart';
import '../../auth/presentation/auth_screen.dart';

class ChatScreenArgs {
  const ChatScreenArgs({
    required this.characterSlug,
    required this.title,
    required this.avatarUrl,
  });

  final String characterSlug;
  final String title;
  final String? avatarUrl;
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.args});

  static const routeName = '/chat';

  final ChatScreenArgs args;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _messageController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  bool _isLoading = true;
  bool _isSending = false;
  bool _didScheduleInitialLoad = false;
  String? _errorText;
  List<ChatMessageItem> _messages = const [];

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_didScheduleInitialLoad) {
      return;
    }
    _didScheduleInitialLoad = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _loadMessages();
      }
    });
  }

  @override
  void dispose() {
    _messageController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _loadMessages() async {
    final scope = AppScope.of(context);
    setState(() {
      _isLoading = true;
      _errorText = null;
    });
    try {
      final payload = await scope.getAuthorizedJson(
          '/api/mobile/chats/${widget.args.characterSlug}/messages');
      final rawItems = payload['items'] as List<dynamic>? ?? const [];
      setState(() {
        _messages = rawItems
            .whereType<Map<String, dynamic>>()
            .map(ChatMessageItem.fromJson)
            .toList(growable: false);
      });
      _scrollToBottom(immediate: true);
    } on ApiException catch (error) {
      if (error.statusCode == 401 && mounted) {
        Navigator.of(context)
            .pushNamedAndRemoveUntil(AuthScreen.routeName, (_) => false);
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

  Future<void> _sendMessage() async {
    final text = _messageController.text.trim();
    if (text.isEmpty) return;

    final scope = AppScope.of(context);
    final optimisticUserMessage = ChatMessageItem(
      id: 'local-user-${DateTime.now().microsecondsSinceEpoch}',
      role: 'user',
      text: text,
      createdAt: null,
    );
    final pendingAssistantMessage = ChatMessageItem(
      id: 'local-assistant-${DateTime.now().microsecondsSinceEpoch}',
      role: 'assistant',
      text: 'Печатает...',
      createdAt: null,
    );

    setState(() {
      _isSending = true;
      _errorText = null;
      _messages = [
        ..._messages,
        optimisticUserMessage,
        pendingAssistantMessage
      ];
    });
    _messageController.clear();
    _scrollToBottom();

    try {
      final history = [
        for (final message in _messages)
          if (message.id != pendingAssistantMessage.id)
            {
              'role': message.role,
              'text': message.text,
            },
      ];
      final payload = await scope.postAuthorizedJson(
        '/api/web-chat',
        body: {
          'character_slug': widget.args.characterSlug,
          'messages': history,
        },
      );
      final rawAssistantMessage =
          payload['message'] as Map<String, dynamic>? ?? const {};
      final assistantMessage = ChatMessageItem(
        id: 'local-assistant-reply-${DateTime.now().microsecondsSinceEpoch}',
        role: rawAssistantMessage['role'] as String? ?? 'assistant',
        text: (rawAssistantMessage['text'] as String? ?? '').trim().isNotEmpty
            ? (rawAssistantMessage['text'] as String).trim()
            : '...',
        createdAt: null,
      );
      setState(() {
        _messages = [
          ..._messages
              .where((message) => message.id != pendingAssistantMessage.id),
          assistantMessage,
        ];
      });
      _scrollToBottom();
    } on ApiException catch (error) {
      if (error.statusCode == 401 && mounted) {
        Navigator.of(context)
            .pushNamedAndRemoveUntil(AuthScreen.routeName, (_) => false);
        return;
      }
      setState(() {
        _messages = _messages
            .where((message) => message.id != pendingAssistantMessage.id)
            .toList(growable: false);
        _errorText = error.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _isSending = false;
        });
      }
    }
  }

  void _scrollToBottom({bool immediate = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scrollController.hasClients) {
        return;
      }
      final target = _scrollController.position.maxScrollExtent;
      if (immediate) {
        _scrollController.jumpTo(target);
        return;
      }
      _scrollController.animateTo(
        target,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.args.title),
        actions: [
          IconButton(
            onPressed: _isLoading ? null : _loadMessages,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : _errorText != null
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(_errorText!, textAlign: TextAlign.center),
                        ),
                      )
                    : _messages.isEmpty
                        ? const Center(
                            child: Padding(
                              padding: EdgeInsets.all(32),
                              child: Text(
                                'Начни общаться со мной, отправь мне текст или позвони.',
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  fontSize: 20,
                                  fontWeight: FontWeight.w600,
                                  color: Color(0xFF6F7897),
                                  height: 1.4,
                                ),
                              ),
                            ),
                          )
                        : ListView.builder(
                            controller: _scrollController,
                            padding: const EdgeInsets.all(16),
                            itemCount: _messages.length,
                            itemBuilder: (context, index) {
                              final message = _messages[index];
                              return _Bubble(
                                text: message.text,
                                isAssistant: message.isAssistant,
                                isPending: message.text == 'Печатает...',
                              );
                            },
                          ),
          ),
          if (_errorText != null && !_isLoading)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Text(
                _errorText!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _messageController,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) {
                        if (!_isSending) {
                          _sendMessage();
                        }
                      },
                      decoration: const InputDecoration(
                        hintText: 'Сообщение',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  FilledButton(
                    onPressed: _isSending ? null : _sendMessage,
                    child: Text(_isSending ? '...' : 'Отпр.'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.text,
    required this.isAssistant,
    this.isPending = false,
  });

  final String text;
  final bool isAssistant;
  final bool isPending;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: isAssistant ? Alignment.centerLeft : Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(14),
        constraints: const BoxConstraints(maxWidth: 320),
        decoration: BoxDecoration(
          color: isAssistant ? Colors.white : const Color(0xFFCCEDE5),
          borderRadius: BorderRadius.circular(18),
        ),
        child: Text(
          text,
          style: TextStyle(
            color: const Color(0xFF2D3557),
            fontSize: 16,
            fontStyle: isPending ? FontStyle.italic : FontStyle.normal,
          ),
        ),
      ),
    );
  }
}
