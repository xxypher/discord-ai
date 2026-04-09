import { spawn } from 'child_process';
import * as fs from 'fs';
import path from 'path';

export async function queryAI(input: string, userMemory: any, shared: any, userId: string): Promise<string> {
  return new Promise((resolve) => {
    const payload = JSON.stringify({ input, userMemory, shared });
    const pythonProcess = spawn('python', ['ai_logic.py', payload]);

    let result = '';
    pythonProcess.stdout.on('data', (data) => result += data.toString());

    pythonProcess.on('close', () => {
      try {
        const parsed = JSON.parse(result);
        
        // AUTO-SAVE NEW FACTS
        if (parsed.new_fact) {
          const { s, a, v } = parsed.new_fact;
          if (!userMemory.personal_info[s]) userMemory.personal_info[s] = {};
          userMemory.personal_info[s][a] = v;
          
          // Save to your memory.json file
          const memPath = path.join(__dirname, '../../memory.json');
          const fullMem = JSON.parse(fs.readFileSync(memPath, 'utf-8'));
          fullMem.users[userId].personal_info = userMemory.personal_info;
          fs.writeFileSync(memPath, JSON.stringify(fullMem, null, 2));
          console.log(`[Auto-Learned] ${s} -> ${a}: ${v}`);
        }
        
        resolve(parsed.reply);
      } catch (e) {
        resolve(result.trim() || "I'm having trouble thinking.");
      }
    });
  });
}