# Flutter App

This directory will host the native mobile client for iOS and Android.

## Planned feature modules

- `lib/features/auth`
- `lib/features/chat_list`
- `lib/features/chat`
- `lib/features/call`
- `lib/features/account`

## Current status

The project now includes a minimal live flow wired to the backend:

- request code: `POST /api/mobile/auth/request-code`
- verify code: `POST /api/mobile/auth/verify-code`
- chat list: `GET /api/mobile/chats`
- chat history: `GET /api/mobile/chats/<slug>/messages`
- send message: `POST /api/mobile/chats/<slug>/messages`

## Run

Start the Flask backend first, then run Flutter with a backend URL:

```bash
flutter pub get
flutter run --dart-define=API_BASE_URL=http://127.0.0.1:5000
```

For iOS simulator or Android emulator, use the host that is reachable from that device.

Examples:

```bash
flutter run --dart-define=API_BASE_URL=http://localhost:5000
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:5000
```

`10.0.2.2` is usually needed for the Android emulator when the backend runs on the host machine.

## Session behavior

- Access and refresh tokens are stored in secure storage.
- On app restart, the client tries to refresh the access token automatically.
- Logout clears the local secure storage and invalidates the refresh token on the backend.
