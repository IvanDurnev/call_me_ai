# Mobile API Contract

## Goal

Build a native Flutter client for iOS and Android that mirrors the current web messenger flow:

1. Sign in with email or phone.
2. Confirm with a one-time code from email or SMS.
3. Open a chat list screen similar to `/web`.
4. Open a character chat.
5. Start text chat first, then add voice call/realtime.

The existing Flask app remains the single backend. The current `/web` page remains the web client and should keep working during the migration.

## Repo Layout

```text
call_me_ai/
  app/
  static/
  templates/
  tests/
  mobile/
    flutter_app/
      lib/
      android/
      ios/
      test/
  docs/
    mobile-api.md
    mobile-roadmap.md
```

## Product Scope

### Phase 1

- Email sign-in with one-time code
- Session/token-based mobile authentication
- Current user endpoint
- Chat list endpoint
- Chat history endpoint
- Send text message endpoint
- Reuse existing call session start/finish endpoints where possible

### Phase 2

- Phone sign-in with one-time SMS code
- Push notifications
- Voice/realtime UX polish
- Subscription/account flows inside mobile UI

## Backend Direction

The current backend already has useful building blocks:

- App users in `AppUser`
- Email OTP logic in `EmailCode`
- Character list and serialization
- Text chat via `/api/web-chat`
- Voice session start/finish via `/api/call-sessions/start` and `/api/call-sessions/<id>/finish`
- Websocket bridge at `/ws/call/<slug>`

The missing layer is a mobile-oriented API contract and mobile auth model.

## Proposed Mobile Endpoints

All new endpoints should live under `/api/mobile/...`.

### Auth

#### `POST /api/mobile/auth/request-code`

Starts an OTP flow for email or phone.

Request:

```json
{
  "login": "user@example.com"
}
```

or

```json
{
  "login": "+79990000000"
}
```

Response:

```json
{
  "ok": true,
  "channel": "email",
  "masked_destination": "u***@example.com",
  "expires_in_seconds": 600,
  "resend_in_seconds": 60,
  "purpose": "mobile_login"
}
```

Notes:

- Backend detects whether `login` is email or phone.
- For phase 1, phone can return `400` with a clear `"SMS login is not enabled yet."`.
- If the user does not exist and login is email, backend may auto-create a lightweight `AppUser` draft or require a separate signup step. Recommended: auto-create only after successful code verification.

#### `POST /api/mobile/auth/verify-code`

Completes login and issues tokens.

Request:

```json
{
  "login": "user@example.com",
  "code": "123456",
  "purpose": "mobile_login",
  "device_name": "Ivan iPhone 15"
}
```

Response:

```json
{
  "ok": true,
  "access_token": "jwt-or-random-token",
  "refresh_token": "refresh-token",
  "expires_in": 3600,
  "user": {
    "id": 12,
    "uuid": "....",
    "name": "Ivan",
    "email": "user@example.com",
    "phone": "",
    "email_verified": true
  }
}
```

#### `POST /api/mobile/auth/refresh`

Request:

```json
{
  "refresh_token": "refresh-token"
}
```

Response:

```json
{
  "ok": true,
  "access_token": "new-access-token",
  "expires_in": 3600
}
```

#### `POST /api/mobile/auth/logout`

Request:

```json
{
  "refresh_token": "refresh-token"
}
```

Response:

```json
{
  "ok": true
}
```

### User

#### `GET /api/mobile/me`

Response:

```json
{
  "ok": true,
  "user": {
    "id": 12,
    "uuid": "....",
    "name": "Ivan",
    "email": "user@example.com",
    "phone": "",
    "email_verified": true,
    "has_call_access": true,
    "remaining_trial_minutes": 1
  }
}
```

### Chat List

#### `GET /api/mobile/chats`

Returns one item per character, shaped for a messenger list.

Response:

```json
{
  "ok": true,
  "items": [
    {
      "chat_id": "hero:anna",
      "character_slug": "anna",
      "title": "Anna",
      "subtitle": "Психолог и собеседник",
      "avatar_url": "https://...",
      "last_message": {
        "role": "assistant",
        "text": "Привет 🙂 Чем помочь?",
        "created_at": "2026-06-08T10:30:00Z"
      },
      "unread_count": 0,
      "can_call": true
    }
  ]
}
```

Implementation note:

- In the short term, if there is no persistent text-message table yet, the list can be derived from characters plus the latest `CallSession.meta_json.conversation_log`.
- Long term, mobile chat should use a dedicated message/session model.

### Chat History

#### `GET /api/mobile/chats/<character_slug>/messages`

Response:

```json
{
  "ok": true,
  "character": {
    "slug": "anna",
    "name": "Anna",
    "avatar_url": "https://..."
  },
  "items": [
    {
      "id": "msg_001",
      "role": "assistant",
      "text": "Привет 🙂 Чем помочь?",
      "created_at": "2026-06-08T10:30:00Z"
    }
  ],
  "next_cursor": null
}
```

### Send Message

#### `POST /api/mobile/chats/<character_slug>/messages`

Request:

```json
{
  "text": "Привет, как дела?"
}
```

Response:

```json
{
  "ok": true,
  "user_message": {
    "id": "msg_user_001",
    "role": "user",
    "text": "Привет, как дела?",
    "created_at": "2026-06-08T10:31:00Z"
  },
  "assistant_message": {
    "id": "msg_assistant_001",
    "role": "assistant",
    "text": "У меня все хорошо. Как твое настроение сегодня?",
    "created_at": "2026-06-08T10:31:01Z"
  }
}
```

Implementation note:

- Short term: this endpoint can wrap the existing `generate_chat_reply` flow used by `/api/web-chat`.
- Recommended next step: persist mobile text messages in a dedicated table instead of only returning transient replies.

### Voice Call

#### `POST /api/mobile/call-sessions/start`

Prefer a mobile alias around the current call session logic.

Request:

```json
{
  "character_slug": "anna",
  "started_from": "mobile"
}
```

Response for OpenAI realtime:

```json
{
  "ok": true,
  "provider": "openai",
  "call_session_id": 321,
  "websocket_url": "wss://example.com/ws/call/anna"
}
```

Response for ElevenLabs realtime:

```json
{
  "ok": true,
  "provider": "elevenlabs",
  "call_session_id": 321,
  "signed_url": "wss://...",
  "conversation_initiation_client_data": {}
}
```

#### `POST /api/mobile/call-sessions/<id>/finish`

Same shape as the current finish endpoint, but authenticated by mobile token.

## Data Model Changes

### Recommended for the first backend slice

1. Add a token model for mobile sessions.
2. Add generalized OTP support for email and phone.
3. Add a persistent mobile chat message model.

### Proposed tables

#### `MobileAuthToken`

- `id`
- `app_user_id`
- `access_token_hash`
- `refresh_token_hash`
- `device_name`
- `last_used_at`
- `expires_at`
- `refresh_expires_at`
- `revoked_at`
- `created_at`

#### `LoginCode`

Generalized replacement or extension for `EmailCode`.

- `id`
- `channel` (`email` or `phone`)
- `destination`
- `purpose`
- `code_hash`
- `app_user_id` nullable
- `expires_at`
- `consumed_at`
- `created_at`

#### `ChatMessage`

- `id`
- `app_user_id`
- `character_slug`
- `role` (`user` or `assistant`)
- `text`
- `source` (`mobile`, `web`, `call_import`)
- `created_at`

## Implementation Order

1. Create `mobile/` repo structure.
2. Add backend mobile auth helpers and token model.
3. Implement `/api/mobile/auth/request-code`.
4. Implement `/api/mobile/auth/verify-code`.
5. Implement auth middleware for bearer tokens.
6. Implement `/api/mobile/me`.
7. Implement `/api/mobile/chats`.
8. Implement `/api/mobile/chats/<slug>/messages` read/write.
9. Add Flutter screens for auth, chat list, chat.
10. Reuse call session APIs inside Flutter.

## Important Constraints

- Do not break current web session auth.
- Do not rename or remove current `/api/web-chat` and `/api/call-sessions/...` endpoints until Flutter is fully switched over.
- Keep the first mobile release text-first; voice can come immediately after auth/chat stabilization.
