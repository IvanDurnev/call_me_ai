class ChatListItem {
  const ChatListItem({
    required this.chatId,
    required this.characterSlug,
    required this.title,
    required this.subtitle,
    required this.avatarUrl,
    required this.lastMessageText,
    required this.canCall,
  });

  final String chatId;
  final String characterSlug;
  final String title;
  final String subtitle;
  final String? avatarUrl;
  final String lastMessageText;
  final bool canCall;

  factory ChatListItem.fromJson(Map<String, dynamic> json) {
    final lastMessage = json['last_message'] as Map<String, dynamic>? ?? const {};
    return ChatListItem(
      chatId: json['chat_id'] as String? ?? '',
      characterSlug: json['character_slug'] as String? ?? '',
      title: json['title'] as String? ?? '',
      subtitle: json['subtitle'] as String? ?? '',
      avatarUrl: json['avatar_url'] as String?,
      lastMessageText: lastMessage['text'] as String? ?? '',
      canCall: json['can_call'] as bool? ?? false,
    );
  }
}

class CharacterSummary {
  const CharacterSummary({
    required this.slug,
    required this.name,
    required this.avatarUrl,
  });

  final String slug;
  final String name;
  final String? avatarUrl;

  factory CharacterSummary.fromJson(Map<String, dynamic> json) {
    return CharacterSummary(
      slug: json['slug'] as String? ?? '',
      name: json['name'] as String? ?? '',
      avatarUrl: json['avatar_url'] as String?,
    );
  }
}

class ChatMessageItem {
  const ChatMessageItem({
    required this.id,
    required this.role,
    required this.text,
    required this.createdAt,
  });

  final String id;
  final String role;
  final String text;
  final String? createdAt;

  bool get isAssistant => role == 'assistant';

  factory ChatMessageItem.fromJson(Map<String, dynamic> json) {
    return ChatMessageItem(
      id: json['id'] as String? ?? '',
      role: json['role'] as String? ?? 'assistant',
      text: json['text'] as String? ?? '',
      createdAt: json['created_at'] as String?,
    );
  }
}
