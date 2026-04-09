import { Client, GatewayIntentBits, Message, TextChannel, ChannelType } from 'discord.js';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import dotenv from 'dotenv';

dotenv.config();

const client = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
        GatewayIntentBits.GuildMembers,
    ],
});

const COOLDOWN_MS = 5000;
const userCooldowns = new Map<string, number>();
const MEMORY_PATH = path.join(__dirname, '../memory.json');

// --- Interfaces ---
interface UserMemory {
    chat_history?: string[];
    last_seen?: number;
    [key: string]: any;
}

interface Memory {
    personal_info: { [displayName: string]: UserMemory };
    server_info: { [serverId: string]: { [key: string]: any } };
}

// New fact shape returned by Python
interface ExtractedFact {
    target: 'self' | 'other' | 'server';
    subject: string;
    key: string;
    value: string;
}

function loadMemory(): Memory {
    try {
        if (!fs.existsSync(MEMORY_PATH)) return { personal_info: {}, server_info: {} };
        const data = JSON.parse(fs.readFileSync(MEMORY_PATH, 'utf8'));
        return { personal_info: data.personal_info || {}, server_info: data.server_info || {} };
    } catch (e) { return { personal_info: {}, server_info: {} }; }
}

function saveMemory(memory: Memory): void {
    try {
        fs.writeFileSync(MEMORY_PATH, JSON.stringify(memory, null, 2));
    } catch (e) { console.error("Memory Save Error:", e); }
}

function updateStoredHistory(memory: Memory, displayName: string, newLine: string) {
    if (!memory.personal_info[displayName]) memory.personal_info[displayName] = {};
    if (!memory.personal_info[displayName].chat_history) memory.personal_info[displayName].chat_history = [];

    const history = memory.personal_info[displayName].chat_history!;
    history.push(newLine);
    if (history.length > 30) {
        memory.personal_info[displayName].chat_history = history.slice(-30);
    }
}

function mergeFactIntoMemory(memory: Memory, fact: ExtractedFact, serverId: string): boolean {
    // Guard: skip malformed facts
    if (!fact || !fact.target || !fact.subject || !fact.key || !fact.value) return false;

    const { target, subject, key, value } = fact;

    if (target === 'server') {
        if (!memory.server_info[serverId]) memory.server_info[serverId] = {};
        memory.server_info[serverId][key] = value;
        console.log(`[Memory] SERVER[${key}] = ${value}`);
        return true;
    }

    // target === 'self' or 'other' — both save to personal_info under subject
    if (!memory.personal_info[subject]) memory.personal_info[subject] = {};
    const existing = memory.personal_info[subject][key];

    if (Array.isArray(existing)) {
        if (existing.includes(value)) return false;
        existing.push(value);
    } else {
        memory.personal_info[subject][key] = [value];
    }

    console.log(`[Memory] ${subject}[${key}] = ${value} (via ${target})`);
    return true;
}

async function passiveObserve(userInput: string, displayName: string, serverId: string): Promise<void> {
    return new Promise((resolve) => {
        if (userInput.split(' ').length < 3) return resolve();

        const payload = JSON.stringify({
            input: userInput,
            userMemory: loadMemory(),         // give Python the known names for fuzzy matching
            userContext: { displayName, serverId },
            observeOnly: true
        });

        const py = spawn('python', ['ai_logic.py', payload]);
        let result = '';
        py.stdout.on('data', (d) => { result += d.toString(); });

        py.on('close', () => {
            try {
                const parsed = JSON.parse(result);
                const fact: ExtractedFact | null = parsed.new_fact ?? null;
                if (fact) {
                    const mem = loadMemory();
                    const learned = mergeFactIntoMemory(mem, fact, serverId);
                    if (learned) saveMemory(mem);
                }
            } catch (_) {}
            resolve();
        });
    });
}

async function askAI(userInput: string, memory: Memory, userData: any, history: string): Promise<any> {
    return new Promise((resolve) => {
        const payload = JSON.stringify({
            input: userInput,
            userMemory: memory,
            userContext: userData,
            history: history
        });

        const py = spawn('python', ['ai_logic.py', payload]);
        let result = '';
        py.stdout.on('data', (data) => { result += data.toString(); });
        py.on('close', () => {
            try { resolve(JSON.parse(result)); }
            catch (e) { resolve({ reply: "brain stalled.", new_fact: null }); }
        });
    });
}

client.on('messageCreate', async (message: Message) => {
    if (message.author.bot || !message.guild) return;

    const displayName = message.member?.user.globalName || message.author.username;
    const cleanInput = message.content.replace(/<@!?\d+>/g, '').trim();
    const serverId = message.guild.id;

    const isMention = message.mentions.has(client.user!.id);
    const isReplyToMe = message.reference
        ? (await message.fetchReference().catch(() => null))?.author.id === client.user!.id
        : false;

    // --- PASSIVE OBSERVATION ---
    if (!isMention && !isReplyToMe) {
        console.log(`[Observe] ${displayName} in ${message.guild.name}: ${cleanInput}`);
        passiveObserve(cleanInput, displayName, serverId);
        return;
    }

    // --- ACTIVE RESPONSE ---
    const now = Date.now();
    const lastTime = userCooldowns.get(message.author.id) || 0;
    if (now - lastTime < COOLDOWN_MS) return;

    if (message.channel.type === ChannelType.GuildText) await (message.channel as TextChannel).sendTyping();

    const memory = loadMemory();
    updateStoredHistory(memory, displayName, `${displayName}: ${cleanInput}`);

    const userData = {
        id: message.author.id,
        displayName,
        serverId,
        roles: message.member?.roles.cache.filter(r => r.name !== '@everyone').map(r => r.name) ?? []
    };

    const historyText = (memory.personal_info[displayName]?.chat_history || []).join('\n');
    const aiResponse = await askAI(cleanInput, memory, userData, historyText);

    await message.reply(aiResponse.reply);
    userCooldowns.set(message.author.id, now);

    updateStoredHistory(memory, displayName, `pez ai: ${aiResponse.reply}`);

    // Handle fact learning with new shape
    const fact: ExtractedFact | null = aiResponse.new_fact ?? null;
    if (fact) mergeFactIntoMemory(memory, fact, serverId);

    memory.personal_info[displayName].last_seen = Date.now();
    saveMemory(memory);
});

client.once('clientReady', () => console.log(`--- ${client.user?.tag} ONLINE ---`));
client.login(process.env.TOKEN);