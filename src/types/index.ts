export interface UserMemory {
  personal_info: Record<string, any>;
  history: { role: string; content: string }[];
}

export interface SharedKnowledge {
  [key: string]: string;
}

export interface BotMemory {
  users: Record<string, UserMemory>;
  shared_knowledge: SharedKnowledge;
}