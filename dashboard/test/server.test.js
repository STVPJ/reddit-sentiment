// Server smoke test: a tiny database, the real config.yaml, and the counting gates.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Database = require('better-sqlite3');
const { createApp, loadConfig } = require('../server');

const config = loadConfig(path.join(__dirname, '..', '..', 'config.yaml'));

function makeDb() {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sent-')), 't.db');
  const db = new Database(file);
  db.exec(`
    CREATE TABLE threads (reddit_id TEXT PRIMARY KEY, subreddit TEXT, title TEXT, url TEXT,
      created_utc INTEGER, num_comments INTEGER, comments_fetched INTEGER,
      comments_cursor INTEGER, selftext TEXT);
    CREATE TABLE items (reddit_id TEXT PRIMARY KEY, kind TEXT, thread_id TEXT, text TEXT,
      score INTEGER, created_utc INTEGER, parent_id TEXT, target_checked INTEGER);
    CREATE TABLE mentions (item_id TEXT, product TEXT, source TEXT, target_p REAL, about REAL,
      sentiment REAL, sentiment_conf REAL, aspect TEXT, aspects TEXT, themes TEXT, model TEXT,
      judged_at TEXT, PRIMARY KEY (item_id, product));`);
  const now = Math.floor(Date.now() / 1000);
  db.prepare("INSERT INTO threads VALUES ('t3_a','webdev','T','https://example.com',?,3,2,0,'')").run(now);
  const rows = [  // id, product, about, sentiment, conf, aspects, themes
    ['t1_1', 'Cursor', 0.9, 4, 0.9, { pricing: 0.8 }, { code_quality: 0.9 }],
    ['t1_2', 'Cursor', 0.9, 0, 0.9, { pricing: 0.1 }, {}],
    ['t1_3', 'Cursor', 0.2, 0, 0.9, {}, {}],       // off-topic: not counted
    ['t1_4', 'Cursor', 0.9, 0, 0.3, {}, {}],       // low confidence: not counted
  ];
  for (const [id, p, ab, se, cf, asp, th] of rows) {
    db.prepare("INSERT INTO items VALUES (?, 'comment', 't3_a', 'x', 1, ?, NULL, 1)").run(id, now);
    db.prepare("INSERT INTO mentions VALUES (?, ?, 'name', NULL, ?, ?, ?, 'general', ?, ?, 'test', 'now')")
      .run(id, p, ab, se, cf, JSON.stringify(asp), JSON.stringify(th));
  }
  db.close();
  return file;
}

async function withServer(fn) {
  const app = createApp({ dbPath: makeDb(), config });
  const server = app.listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try { await fn(base); } finally { server.close(); app.locals.db.close(); }
}

test('config endpoint exposes products, dimensions and themes from config.yaml', () => withServer(async (base) => {
  const cfg = await (await fetch(`${base}/api/config`)).json();
  assert.strictEqual(cfg.primary_product, config.primaryProduct);
  assert.deepStrictEqual(cfg.dimensions, ['overall', ...config.aspects]);
  assert.ok(cfg.themes.some((t) => t.key === 'code_quality'));
}));

test('only on-topic, confident mentions are counted', () => withServer(async (base) => {
  const stats = await (await fetch(`${base}/api/stats`)).json();
  assert.strictEqual(stats.counted_mentions, 2);
  const [cursor] = await (await fetch(`${base}/api/overview`)).json();
  assert.strictEqual(cursor.total_posts, 2);
  assert.strictEqual(cursor.dimensions.overall.positive, 1);
  assert.strictEqual(cursor.dimensions.pricing.applicable, 1);   // multi-label aspect gate
}));

test('themes and validation', () => withServer(async (base) => {
  const themes = await (await fetch(`${base}/api/themes?competitor=Cursor`)).json();
  assert.deepStrictEqual(themes.map((t) => t.theme), ['code_quality']);
  const bad = await fetch(`${base}/api/trends?competitor=Cursor&dimension=nope`);
  assert.strictEqual(bad.status, 400);
}));

test('a demo database uses the fictional demo config and says so', async () => {
  const dbPath = makeDb();
  const db = new Database(dbPath);
  db.exec("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('demo', '1');");
  db.close();
  const demoConfig = loadConfig(path.join(__dirname, '..', '..', 'config.demo.yaml'));
  const app = createApp({ dbPath, config, demoConfig });
  const server = app.listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  try {
    const cfg = await (await fetch(`http://127.0.0.1:${server.address().port}/api/config`)).json();
    assert.strictEqual(cfg.demo, true);
    assert.strictEqual(cfg.primary_product, 'Codewren');
    assert.ok(!cfg.products.includes(config.primaryProduct));
  } finally { server.close(); app.locals.db.close(); }
});
