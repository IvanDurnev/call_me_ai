# Mobile Roadmap

## Decision

We are building the iOS/Android app as a Flutter client inside the current repository.

Current target path:

```text
/Users/ivandurnev/deva/call_me_ai/mobile/flutter_app
```

## Why This Setup

- The backend is already the source of truth for users, characters, calls, and billing.
- The current `/web` flow should continue to work during mobile development.
- One repo keeps backend and Flutter API changes coordinated.
- We can split repositories later if mobile gets an independent team or release cycle.

## First Delivery Slice

The first useful milestone is not full parity with `/web`. It is:

1. Email OTP login
2. Chat list screen
3. Character chat screen
4. Text message send/receive
5. Read-only account basics

Voice calls should be wired after that baseline is stable.

## Backend Work Breakdown

### Slice A

- Add `/api/mobile/auth/request-code`
- Add `/api/mobile/auth/verify-code`
- Add token storage and token auth middleware
- Add `/api/mobile/me`

### Slice B

- Add `/api/mobile/chats`
- Add `/api/mobile/chats/<slug>/messages`
- Reuse existing chat generation logic for replies
- Persist text messages in a dedicated table

### Slice C

- Add `/api/mobile/call-sessions/start`
- Add `/api/mobile/call-sessions/<id>/finish`
- Validate Flutter compatibility with current websocket/audio flow

## Flutter Work Breakdown

### Slice A

- App shell
- Routing
- API client
- Secure token storage
- Auth screens

### Slice B

- Chat list screen
- Chat screen
- Message composer
- Loading and error states

### Slice C

- Voice call screen
- Realtime websocket client
- Audio permissions and session management

## Immediate Next Step

Implement backend Slice A first, because the Flutter app should not be blocked on auth design.
