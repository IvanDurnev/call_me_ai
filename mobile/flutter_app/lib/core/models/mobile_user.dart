class MobileUser {
  const MobileUser({
    required this.id,
    required this.uuid,
    required this.name,
    required this.email,
    required this.phone,
    required this.emailVerified,
    required this.hasCallAccess,
    required this.remainingTrialMinutes,
  });

  final int id;
  final String uuid;
  final String name;
  final String email;
  final String phone;
  final bool emailVerified;
  final bool hasCallAccess;
  final int remainingTrialMinutes;

  factory MobileUser.fromJson(Map<String, dynamic> json) {
    return MobileUser(
      id: (json['id'] as num?)?.toInt() ?? 0,
      uuid: json['uuid'] as String? ?? '',
      name: json['name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      phone: json['phone'] as String? ?? '',
      emailVerified: json['email_verified'] as bool? ?? false,
      hasCallAccess: json['has_call_access'] as bool? ?? false,
      remainingTrialMinutes: (json['remaining_trial_minutes'] as num?)?.toInt() ?? 0,
    );
  }
}
