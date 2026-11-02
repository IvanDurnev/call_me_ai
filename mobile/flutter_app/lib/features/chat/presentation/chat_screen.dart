import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../../core/models/chat_models.dart';
import '../../../core/network/api_client.dart';
import '../../../core/session/app_scope.dart';
import '../../auth/presentation/auth_screen.dart';

class ChatScreenArgs {
  const ChatScreenArgs({
    required this.characterSlug,
    required this.title,
    required this.avatarUrl,
    required this.canCall,
  });

  final String characterSlug;
  final String title;
  final String? avatarUrl;
  final bool canCall;
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.args});

  static const routeName = '/chat';

  final ChatScreenArgs args;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  static const int _callSampleRate = 16000;

  final TextEditingController _messageController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final FlutterSoundRecorder _callRecorder = FlutterSoundRecorder();
  final FlutterSoundPlayer _callPlayer = FlutterSoundPlayer();
  bool _isLoading = true;
  bool _isSending = false;
  bool _isStartingCall = false;
  bool _hasDraftText = false;
  bool _didScheduleInitialLoad = false;
  int? _activeCallSessionId;
  WebSocket? _callSocket;
  StreamController<Uint8List>? _micStreamController;
  StreamSubscription<Uint8List>? _micStreamSubscription;
  Timer? _assistantAudioGateTimer;
  String? _callProvider;
  String? _providerConversationId;
  int _sentAudioChunkCount = 0;
  bool _assistantAudioActive = false;
  List<Map<String, String>> _localCallConversationLog = const [];
  String? _errorText;
  String? _callStatusText;
  List<ChatMessageItem> _messages = const [];

  @override
  void initState() {
    super.initState();
    _messageController.addListener(_syncDraftText);
  }

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
    _stopAudioPipeline();
    _callSocket?.close();
    _messageController.removeListener(_syncDraftText);
    _messageController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _syncDraftText() {
    final hasText = _messageController.text.trim().isNotEmpty;
    if (hasText == _hasDraftText) {
      return;
    }
    setState(() {
      _hasDraftText = hasText;
    });
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

  Future<void> _sendMessage([String? submittedText]) async {
    if (_isSending) {
      return;
    }
    final text = (submittedText ?? _messageController.text).trim();
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
    _hasDraftText = false;
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
      _replacePendingWithWebChatReply(payload, pendingAssistantMessage.id);
      _scrollToBottom();
    } on ApiException catch (error) {
      if (error.statusCode == 401 && mounted) {
        try {
          final payload = await scope.postAuthorizedJson(
            '/api/mobile/chats/${widget.args.characterSlug}/messages',
            body: {'text': text},
          );
          _replacePendingWithMobileReply(
            payload,
            optimisticUserMessage.id,
            pendingAssistantMessage.id,
          );
          _scrollToBottom();
          return;
        } on ApiException {
          if (!mounted) {
            return;
          }
          Navigator.of(context)
              .pushNamedAndRemoveUntil(AuthScreen.routeName, (_) => false);
          return;
        }
      }
      try {
        final payload = await scope.postAuthorizedJson(
          '/api/mobile/chats/${widget.args.characterSlug}/messages',
          body: {'text': text},
        );
        _replacePendingWithMobileReply(
          payload,
          optimisticUserMessage.id,
          pendingAssistantMessage.id,
        );
        _scrollToBottom();
        return;
      } on ApiException catch (fallbackError) {
        _showSendError(pendingAssistantMessage.id, fallbackError.message);
      }
    } catch (error) {
      _showSendError(
        pendingAssistantMessage.id,
        'Не удалось отправить сообщение: $error',
      );
    } finally {
      if (mounted) {
        setState(() {
          _isSending = false;
        });
      }
    }
  }

  Future<void> _startCall() async {
    if (_isStartingCall || _activeCallSessionId != null) {
      return;
    }
    final scope = AppScope.of(context);
    setState(() {
      _isStartingCall = true;
      _errorText = null;
    });
    try {
      final payload = await scope.postAuthorizedJson(
        '/api/call-sessions/start',
        body: {
          'character_slug': widget.args.characterSlug,
          'started_from': 'mobile',
        },
      );
      final callSessionId = payload['call_session_id'] as int?;
      setState(() {
        _activeCallSessionId = callSessionId;
        _callStatusText = 'Соединение...';
      });
      await _connectCallSocket(payload);
      if (!mounted) {
        return;
      }
      await _showCallStartedDialog(payload);
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
          _isStartingCall = false;
        });
      }
    }
  }

  Future<void> _finishActiveCall({String reason = 'manual'}) async {
    final callSessionId = _activeCallSessionId;
    if (callSessionId == null) {
      return;
    }
    final scope = AppScope.of(context);
    try {
      await _stopAudioPipeline();
      _callSocket?.add(jsonEncode({'type': 'call.stop'}));
      await _callSocket?.close();
      await scope.postAuthorizedJson(
        '/api/call-sessions/$callSessionId/finish',
        body: {
          'reason': reason,
          'provider': _callProvider,
          'conversation_id': _providerConversationId,
          'local_conversation_log': _localCallConversationLog,
        },
      );
    } on ApiException catch (error) {
      setState(() {
        _errorText = error.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _activeCallSessionId = null;
          _callSocket = null;
          _callStatusText = null;
          _callProvider = null;
          _providerConversationId = null;
          _sentAudioChunkCount = 0;
          _assistantAudioActive = false;
          _localCallConversationLog = const [];
        });
      }
    }
  }

  Future<void> _connectCallSocket(Map<String, dynamic> payload) async {
    final provider = payload['provider'] as String? ?? 'openai';
    final websocketUrl = payload['websocket_url'] as String?;
    final signedUrl = payload['signed_url'] as String?;
    final socketUrl = provider == 'elevenlabs' ? signedUrl : websocketUrl;
    if (socketUrl == null || socketUrl.trim().isEmpty) {
      setState(() {
        _callStatusText = 'Аудио endpoint не вернулся.';
      });
      return;
    }

    final scope = AppScope.of(context);
    final socket = await WebSocket.connect(socketUrl);
    _callSocket = socket;
    _callProvider = provider;
    socket.listen(
      _handleCallSocketMessage,
      onDone: () {
        unawaited(_stopAudioPipeline());
        if (!mounted) {
          return;
        }
        setState(() {
          _callStatusText = 'Соединение завершено.';
          _callSocket = null;
        });
      },
      onError: (Object error) {
        unawaited(_stopAudioPipeline());
        if (!mounted) {
          return;
        }
        setState(() {
          _callStatusText = 'Ошибка соединения: $error';
        });
      },
    );
    if (provider == 'elevenlabs') {
      socket.add(jsonEncode(payload['conversation_initiation_client_data']));
      await _startElevenLabsAudioPipeline(socket);
    } else {
      socket.add(
        jsonEncode({
          'type': 'call.start',
          'app_user_id': scope.session.user?.id,
          'character_slug': widget.args.characterSlug,
          'started_from': 'mobile',
          'call_session_id': payload['call_session_id'],
        }),
      );
    }
    setState(() {
      _callStatusText = 'На линии';
    });
  }

  Future<void> _startElevenLabsAudioPipeline(WebSocket socket) async {
    final microphoneStatus = await Permission.microphone.request();
    if (!microphoneStatus.isGranted) {
      setState(() {
        _callStatusText = 'Нет доступа к микрофону.';
      });
      return;
    }

    await _stopAudioPipeline();
    await _callRecorder.openRecorder();
    await _callPlayer.openPlayer();
    await _callPlayer.startPlayerFromStream(
      codec: Codec.pcm16,
      interleaved: true,
      sampleRate: _callSampleRate,
      numChannels: 1,
      bufferSize: 2048,
    );

    _micStreamController = StreamController<Uint8List>();
    _micStreamSubscription = _micStreamController!.stream.listen((chunk) {
      if (_callProvider != 'elevenlabs' ||
          socket.readyState != WebSocket.open ||
          _assistantAudioActive) {
        return;
      }
      _sentAudioChunkCount += 1;
      if (_sentAudioChunkCount == 1 || _sentAudioChunkCount % 50 == 0) {
        setState(() {
          _callStatusText = 'На линии. Микрофон отправляет звук.';
        });
      }
      socket.add(
        jsonEncode({
          'user_audio_chunk': base64Encode(chunk),
        }),
      );
    });
    await _startMicrophoneRecorder();
    setState(() {
      _callStatusText = 'На линии. Микрофон включён.';
    });
  }

  Future<void> _startMicrophoneRecorder() async {
    if (_micStreamController == null || _callRecorder.isRecording) {
      return;
    }
    await _callRecorder.startRecorder(
      toStream: _micStreamController!.sink,
      codec: Codec.pcm16,
      sampleRate: _callSampleRate,
      numChannels: 1,
      bufferSize: 2048,
      audioSource: Platform.isAndroid
          ? AudioSource.voice_communication
          : AudioSource.microphone,
      enableEchoCancellation: true,
      enableNoiseSuppression: true,
      enableVoiceProcessing: true,
    );
  }

  Future<void> _stopMicrophoneRecorder() async {
    try {
      if (_callRecorder.isRecording) {
        await _callRecorder.stopRecorder();
      }
    } catch (_) {}
  }

  Future<void> _stopAudioPipeline() async {
    _assistantAudioGateTimer?.cancel();
    _assistantAudioGateTimer = null;
    _assistantAudioActive = false;
    await _micStreamSubscription?.cancel();
    _micStreamSubscription = null;
    await _micStreamController?.close();
    _micStreamController = null;
    await _stopMicrophoneRecorder();
    try {
      if (_callPlayer.isPlaying) {
        await _callPlayer.stopPlayer();
      }
    } catch (_) {}
    try {
      await _callRecorder.closeRecorder();
    } catch (_) {}
    try {
      await _callPlayer.closePlayer();
    } catch (_) {}
  }

  void _handleCallSocketMessage(dynamic event) {
    if (event is! String) {
      return;
    }
    final payload = jsonDecode(event) as Map<String, dynamic>;
    final type = payload['type'] as String?;
    if (type == 'ping') {
      _callSocket?.add(
        jsonEncode({
          'type': 'pong',
          'event_id':
              (payload['ping_event'] as Map<String, dynamic>?)?['event_id'],
        }),
      );
      return;
    }
    final conversationMetadata =
        payload['conversation_initiation_metadata_event']
                as Map<String, dynamic>? ??
            payload['conversation_initiation_metadata']
                as Map<String, dynamic>? ??
            const {};
    _providerConversationId =
        conversationMetadata['conversation_id'] as String? ??
            payload['conversation_id'] as String? ??
            _providerConversationId;
    if (type == 'conversation_initiation_metadata') {
      setState(() {
        _callStatusText = 'Аудиосессия готова.';
      });
      return;
    }
    if (type == 'audio') {
      final audioBase64 = ((payload['audio_event']
                  as Map<String, dynamic>?)?['audio_base_64'] as String? ??
              '')
          .trim();
      if (audioBase64.isNotEmpty) {
        _markAssistantAudioActive();
        _callPlayer.feedUint8FromStream(base64Decode(audioBase64));
      }
      return;
    }
    if (type == 'call.ready') {
      final character =
          payload['character'] as Map<String, dynamic>? ?? const {};
      setState(() {
        _callStatusText =
            '${character['name'] ?? widget.args.title} готов говорить.';
      });
      return;
    }
    if (type == 'user_transcript') {
      final transcript = ((payload['user_transcription_event']
                  as Map<String, dynamic>?)?['user_transcript'] as String? ??
              '')
          .trim();
      _appendCallTranscript('user', transcript);
      return;
    }
    if (type == 'agent_response' || type == 'agent_response_correction') {
      final eventKey = type == 'agent_response'
          ? 'agent_response_event'
          : 'agent_response_correction_event';
      final textKey = type == 'agent_response'
          ? 'agent_response'
          : 'corrected_agent_response';
      final transcript =
          ((payload[eventKey] as Map<String, dynamic>?)?[textKey] as String? ??
                  '')
              .trim();
      _appendCallTranscript('assistant', transcript);
      return;
    }
    if (type == 'call.transcript') {
      final transcript = (payload['transcript'] as String? ?? '').trim();
      final role = payload['role'] as String? ?? 'assistant';
      _appendCallTranscript(role == 'user' ? 'user' : 'assistant', transcript);
      return;
    }
    if (type == 'error') {
      setState(() {
        _callStatusText = payload['message'] as String? ?? 'Ошибка звонка.';
      });
    }
  }

  void _appendCallTranscript(String role, String transcript) {
    if (transcript.isEmpty) {
      return;
    }
    final normalizedRole = role == 'user' ? 'user' : 'assistant';
    setState(() {
      _localCallConversationLog = [
        ..._localCallConversationLog,
        {'role': normalizedRole, 'text': transcript},
      ];
      _messages = [
        ..._messages,
        ChatMessageItem(
          id: 'call-transcript-${DateTime.now().microsecondsSinceEpoch}',
          role: normalizedRole,
          text: transcript,
          createdAt: null,
        ),
      ];
    });
    _scrollToBottom();
  }

  void _markAssistantAudioActive() {
    _assistantAudioActive = true;
    unawaited(_stopMicrophoneRecorder());
    _assistantAudioGateTimer?.cancel();
    _assistantAudioGateTimer = Timer(const Duration(milliseconds: 1200), () {
      _assistantAudioActive = false;
      if (!mounted || _activeCallSessionId == null) {
        return;
      }
      unawaited(_startMicrophoneRecorder());
      setState(() {
        _callStatusText = 'На линии. Микрофон включён.';
      });
    });
  }

  Future<void> _showCallStartedDialog(Map<String, dynamic> payload) async {
    final provider = payload['provider'] as String? ?? 'unknown';
    final hasAudioEndpoint =
        payload['websocket_url'] != null || payload['signed_url'] != null;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) {
        return AlertDialog(
          title: Text('Звонок: ${widget.args.title}'),
          content: Text(
            hasAudioEndpoint
                ? '${_callStatusText ?? 'Сессия звонка создана.'}\nПровайдер: $provider.'
                : 'Сессия звонка создана, но аудио endpoint не вернулся.',
          ),
          actions: [
            TextButton(
              onPressed: () async {
                Navigator.of(dialogContext).pop();
                await _finishActiveCall();
              },
              child: const Text('Завершить'),
            ),
          ],
        );
      },
    );
  }

  void _replacePendingWithWebChatReply(
    Map<String, dynamic> payload,
    String pendingAssistantMessageId,
  ) {
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
        ..._messages.where(
          (message) => message.id != pendingAssistantMessageId,
        ),
        assistantMessage,
      ];
    });
  }

  void _replacePendingWithMobileReply(
    Map<String, dynamic> payload,
    String optimisticUserMessageId,
    String pendingAssistantMessageId,
  ) {
    final userMessage = ChatMessageItem.fromJson(
      payload['user_message'] as Map<String, dynamic>? ?? const {},
    );
    final assistantMessage = ChatMessageItem.fromJson(
      payload['assistant_message'] as Map<String, dynamic>? ?? const {},
    );
    setState(() {
      _messages = [
        ..._messages.where(
          (message) =>
              message.id != optimisticUserMessageId &&
              message.id != pendingAssistantMessageId,
        ),
        userMessage,
        assistantMessage,
      ];
    });
  }

  void _showSendError(String pendingAssistantMessageId, String message) {
    final assistantMessage = ChatMessageItem(
      id: 'local-assistant-error-${DateTime.now().microsecondsSinceEpoch}',
      role: 'assistant',
      text: message,
      createdAt: null,
    );
    setState(() {
      _messages = [
        ..._messages.where(
          (message) => message.id != pendingAssistantMessageId,
        ),
        assistantMessage,
      ];
      _errorText = message;
    });
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
        title: Row(
          children: [
            _ChatHeaderAvatar(
              title: widget.args.title,
              avatarUrl: widget.args.avatarUrl,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                widget.args.title,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            onPressed: !widget.args.canCall || _isStartingCall
                ? null
                : _activeCallSessionId == null
                    ? _startCall
                    : () => _finishActiveCall(),
            icon: _isStartingCall
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : Icon(
                    _activeCallSessionId == null ? Icons.call : Icons.call_end,
                  ),
            tooltip: widget.args.canCall ? 'Позвонить' : 'Нет доступных минут',
          ),
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
                : _errorText != null && _messages.isEmpty
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
                        : Scrollbar(
                            controller: _scrollController,
                            child: ListView.builder(
                              controller: _scrollController,
                              keyboardDismissBehavior:
                                  ScrollViewKeyboardDismissBehavior.onDrag,
                              physics: const AlwaysScrollableScrollPhysics(),
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
              child: TextField(
                controller: _messageController,
                textInputAction: TextInputAction.send,
                onSubmitted: (value) {
                  _sendMessage(value);
                },
                decoration: InputDecoration(
                  hintText: 'Сообщение',
                  border: const OutlineInputBorder(),
                  suffixIcon: IconButton(
                    onPressed: _isSending || !_hasDraftText
                        ? null
                        : () => _sendMessage(),
                    icon: const Icon(Icons.send),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ChatHeaderAvatar extends StatelessWidget {
  const _ChatHeaderAvatar({
    required this.title,
    required this.avatarUrl,
  });

  final String title;
  final String? avatarUrl;

  @override
  Widget build(BuildContext context) {
    final normalizedAvatarUrl = avatarUrl?.trim();
    final fallbackLetter = title.trim().isEmpty ? '?' : title.trim()[0];
    return CircleAvatar(
      radius: 18,
      backgroundColor: const Color(0xFFCCEDE5),
      foregroundImage:
          normalizedAvatarUrl == null || normalizedAvatarUrl.isEmpty
              ? null
              : NetworkImage(normalizedAvatarUrl),
      child: Text(
        fallbackLetter,
        style: const TextStyle(
          color: Color(0xFF2D3557),
          fontSize: 16,
          fontWeight: FontWeight.w700,
        ),
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
