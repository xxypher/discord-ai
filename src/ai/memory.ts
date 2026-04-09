import fs from 'fs';
import path from 'path';
import { BotMemory, UserMemory } from '../types';

const MEMORY_PATH = path.join(__dirname, '../../memory.json');

export class MemoryManager {
  private data: BotMemory;

  constructor() {
    if (fs.existsSync(MEMORY_PATH)) {
      this.data = JSON.parse(fs.readFileSync(MEMORY_PATH, 'utf-8'));
    } else {
      this.data = { users: {}, shared_knowledge: {} };
    }
  }

  getUser(userId: string): UserMemory {
    if (!this.data.users[userId]) {
      this.data.users[userId] = { personal_info: {}, history: [] };
    }
    return this.data.users[userId];
  }

  save(userId: string, update: Partial<UserMemory>, shared?: Record<string, string>) {
    this.data.users[userId] = { ...this.getUser(userId), ...update };
    if (shared) {
      this.data.shared_knowledge = { ...this.data.shared_knowledge, ...shared };
    }
    fs.writeFileSync(MEMORY_PATH, JSON.stringify(this.data, null, 2));
  }

  getShared() {
    return this.data.shared_knowledge;
  }
}