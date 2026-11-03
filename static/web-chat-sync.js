(() => {
  class ChatSyncClient {
    constructor(userId, onChange) {
      this.userId = userId;
      this.onChange = onChange;
      this.messages = new Map();
      this.requests = new Map();
      this.pendingSlugs = new Set();
      this.cursor = 0;
      this.loaded = false;
      this.error = "";
      this.revision = 0;
      this.syncing = null;
      this.outboxKey = `call_me_ai.chat_outbox.v1:${userId}`;
      this.outbox = this.readOutbox();
    }

    readOutbox() {
      try {
        const raw = localStorage.getItem(this.outboxKey);
        const rows = raw ? JSON.parse(raw) : (this.outbox || []);
        return Array.isArray(rows) ? rows.filter(x => x?.request_id && x?.character_slug && typeof x.text === "string") : [];
      } catch { return this.outbox || []; }
    }

    saveOutbox() {
      // Unsent messages are scoped to the signed-in account, unlike the legacy cache.
      try { localStorage.setItem(this.outboxKey, JSON.stringify(this.outbox)); } catch { /* Keep the in-memory outbox. */ }
    }

    queueMessage(slug, text) {
      this.outbox = this.readOutbox();
      const entry = {request_id: crypto.randomUUID(), character_slug: slug, text};
      this.outbox.push(entry);
      this.saveOutbox();
      this.revision++;
      return entry;
    }

    thread(slug) {
      const rows = Array.from(this.messages.values()).filter(m => m.character_slug === slug);
      rows.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.sequence - b.sequence);
      for (const entry of this.outbox) {
        if (entry.character_slug !== slug) continue;
        const request = this.requests.get(entry.request_id);
        if (request?.user_message_id && this.messages.has(`msg_${request.user_message_id}`)) continue;
        rows.push({id: `pending_${entry.request_id}`, role: "user", text: entry.text});
      }
      return rows;
    }

    async sync() {
      if (this.syncing) return this.syncing;
      this.syncing = this.load();
      try { return await this.syncing; }
      finally { this.syncing = null; }
    }

    async load() {
      try {
        let more;
        do {
          const response = await fetch(`/api/web-chat/history?after_id=${this.cursor}`, {
            cache: "no-store", signal: window.AbortSignal?.timeout?.(15000),
          });
          const payload = await response.json().catch(() => ({ok: false, error: "Не удалось связаться с сервером. Повторите попытку."}));
          if ([401, 403].includes(response.status) || (payload.ok && payload.user_id !== this.userId)) {
            this.loaded = false;
            this.messages.clear();
            this.outbox = [];
            this.revision++;
            throw new Error("Вход в аккаунт изменился. Обновите страницу.");
          }
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Не удалось загрузить переписку.");
          for (const message of payload.items) this.messages.set(message.id, message);
          if (payload.items.length) this.revision++;
          this.cursor = payload.cursor;
          this.pendingSlugs = new Set(payload.pending_slugs || []);
          this.requests = new Map((payload.requests || []).map(r => [r.request_id, r]));
          const before = this.outbox.length;
          this.outbox = this.readOutbox().filter(entry => this.requests.get(entry.request_id)?.status !== "done");
          if (before !== this.outbox.length) { this.saveOutbox(); this.revision++; }
          more = payload.has_more;
        } while (more);
        this.loaded = true;
        this.error = "";
      } catch (error) {
        this.error = error.message || "Нет соединения. Переписка обновится после восстановления связи.";
      }
      this.onChange(this);
      return !this.error;
    }

    async sendMessage(entry) {
      try {
        const response = await fetch("/api/web-chat", {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(entry),
          signal: window.AbortSignal?.timeout?.(240000),
        });
        const payload = await response.json().catch(() => ({ok: false, error: "Не удалось связаться с сервером. Повторите попытку."}));
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Не удалось отправить сообщение.");
        this.outbox = this.readOutbox().filter(item => item.request_id !== entry.request_id);
        this.saveOutbox();
        this.revision++;
        if (this.syncing) await this.syncing;
        await this.sync();
        return payload;
      } catch (error) {
        await this.sync();
        if (!this.outbox.some(item => item.request_id === entry.request_id)) return;
        throw error;
      }
    }

    start() {
      void this.sync();
      window.setInterval(() => { if (!document.hidden) void this.sync(); }, 4000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) void this.sync(); });
      window.addEventListener("focus", () => { void this.sync(); });
      window.addEventListener("online", () => { void this.sync(); });
      window.addEventListener("storage", event => {
        if (event.key === this.outboxKey) { this.outbox = this.readOutbox(); this.revision++; void this.sync(); }
      });
    }
  }
  window.ChatSyncClient = ChatSyncClient;
})();
