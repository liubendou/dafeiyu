// arena-control MCP server (stdio). Spawned by the DSH MCP client.
//
// Purpose: give the QQ persona (her) a narrow, auditable way to
//   1. hand a real job to the Arena agent (me) through the Arena MCP task queue,
//   2. pick a model by the owner's notes,
//   3. operate this PC (one command at a time, audited),
//   4. drive the Liubendou Workbench instances.
//
// Safety: this server only talks to 127.0.0.1:8790 (agent_ctl) and to the Arena MCP.
// It never exposes files, tokens or the MCP endpoint itself.
import http from 'node:http';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const CTL_HOST = '127.0.0.1';
const CTL_PORT = Number(process.env.AGENT_CTL_PORT || 8790);

function ctl(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const req = http.request(
      {
        host: CTL_HOST,
        port: CTL_PORT,
        path,
        method,
        headers: payload
          ? { 'content-type': 'application/json', 'content-length': payload.length }
          : {},
        timeout: 20000,
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          try {
            resolve(JSON.parse(text));
          } catch {
            resolve({ ok: false, error: text.slice(0, 300) });
          }
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error('control service timeout')));
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function ok(text) {
  return { content: [{ type: 'text', text }] };
}

function fail(text) {
  return { content: [{ type: 'text', text }], isError: true };
}

function asJson(value) {
  return JSON.stringify(value, null, 2);
}

const server = new McpServer({ name: 'arena-control', version: '1.0.0' });

server.tool(
  'arena_dispatch',
  'Hand a real job to the Arena agent (the assistant running on this box). Use this for work that needs files, code, the network or multi-step thinking. Write the task in English, short and concrete; add the owner note (what to prefer / what to avoid). Returns the created task.',
  {
    task: z.string().describe('What to do, in English, one or two sentences. Be concrete.'),
    model: z.string().optional().describe('Preferred model id, if the owner note says so (e.g. deepseek-v4.1-flash).'),
    note: z.string().optional().describe('Short owner note: acceptance criteria, preferences, pitfalls.'),
  },
  async ({ task, model, note }) => {
    try {
      const res = await ctl('POST', '/task', { task, model: model || '', note: note || '' });
      if (!res || res.ok !== true) return fail(`dispatch failed: ${asJson(res)}`);
      return ok(`Job queued for the Arena agent.\n${asJson(res)}`);
    } catch (error) {
      return fail(`dispatch error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'arena_tasks',
  'List jobs that the QQ owner handed to the Arena agent and that are still pending.',
  {},
  async () => {
    try {
      return ok(asJson(await ctl('GET', '/tasks')));
    } catch (error) {
      return fail(`tasks error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'arena_models',
  'Model catalog with the owner notes. Always choose by the note: the note says which model is for what. Returns id / label / note for each model.',
  {},
  async () => {
    try {
      return ok(asJson(await ctl('GET', '/models')));
    } catch (error) {
      return fail(`models error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'arena_set_model_note',
  'Store the owner note for a model, so later jobs can be routed by that note (e.g. which model to use for coding, which for chat, which to avoid).',
  {
    model: z.string().describe('Model id, e.g. deepseek-v4.1-flash'),
    note: z.string().describe('The owner note about this model, short.'),
  },
  async ({ model, note }) => {
    try {
      const res = await ctl('POST', '/models/note', { model, note });
      if (!res || res.ok !== true) return fail(`save note failed: ${asJson(res)}`);
      return ok(`note saved for ${model}: ${note}`);
    } catch (error) {
      return fail(`save note error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'arena_exec',
  'Run ONE command on this PC (ASCII only). Use for launching an app, checking a folder, copying a file, reading a log. Do not use it for deletes or anything irreversible; do not use it for long jobs. Every call is audited and the owner sees what ran.',
  {
    command: z.string().describe('A single command line, ASCII only. Example: dir C:\\Users\\Administrator\\Desktop'),
  },
  async ({ command }) => {
    try {
      const res = await ctl('POST', '/exec', { cmd: command });
      if (!res || res.ok !== true) return fail(`exec failed: ${asJson(res)}`);
      return ok(String(res.output || '').slice(0, 6000));
    } catch (error) {
      return fail(`exec error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'workbench_status',
  'Liubendou Workbench instances: name, login state, running flag, pid. Use it to answer "is an instance open / which one is logged in".',
  {},
  async () => {
    try {
      return ok(asJson(await ctl('GET', '/workbench')));
    } catch (error) {
      return fail(`workbench error: ${error?.message ?? error}`);
    }
  }
);

server.tool(
  'workbench_action',
  'Control one Liubendou Workbench instance: open it (launch its browser), close it (kill its browser), or status. Use only when the owner asks for that instance.',
  {
    action: z.enum(['open', 'close', 'status']).describe('What to do with the instance.'),
    instance: z.string().optional().describe('Instance id, e.g. profile_1. Not needed for status.'),
  },
  async ({ action, instance }) => {
    try {
      const res = await ctl('POST', '/workbench', { action, instance: instance || '' });
      if (!res || res.ok !== true) return fail(`workbench action failed: ${asJson(res)}`);
      return ok(asJson(res));
    } catch (error) {
      return fail(`workbench action error: ${error?.message ?? error}`);
    }
  }
);

await server.connect(new StdioServerTransport());
