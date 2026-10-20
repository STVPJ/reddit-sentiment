// Sentiment dashboard: reads the pipeline's SQLite store and config.yaml.
//
// Only "counted" mentions appear: the judge said the text is really about the product
// (about >= about_min) and was confident about the sentiment (sentiment_conf >= conf_min).
// The 0..4 sentiment is bucketed: < 1.5 negative, > 2.5 positive, else neutral.
// Every counted mention feeds the "overall" dimension, and each aspect the judge marked
// present (probability >= label_min). Thresholds, aspects and themes all come from the
// same config.yaml the pipeline uses, so the two can never count different mentions.

const express = require('express');
const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

const ROOT = path.resolve(__dirname, '..');

// YAML with an optional `extends: <file>`; top-level keys here replace the base's.
function readRaw(file) {
  const raw = YAML.parse(fs.readFileSync(file, 'utf8')) || {};
  const base = raw.extends;
  delete raw.extends;
  return base ? { ...readRaw(path.resolve(path.dirname(file), base)), ...raw } : raw;
}

function loadConfig(file) {
  const raw = readRaw(file);
  const t = raw.thresholds || {};
  const themes = Object.entries(raw.themes || {}).map(([key, v]) => ({
    key,
    label: (v && v.label) || key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
  }));
  const products = (raw.products || []).map((p) => p.name);
  const cfg = {
    title: raw.title || 'Reddit Sentiment',
    primaryProduct: raw.primary_product || products[0],
    products,
    aspects: Object.keys(raw.aspects || {}),
    themes,
    aboutMin: Number(t.about_min),
    confMin: Number(t.conf_min),
    labelMin: Number(t.label_min),
    minN: Number(t.min_n),
  };
  for (const k of ['aboutMin', 'confMin', 'labelMin', 'minN']) {
    if (!Number.isFinite(cfg[k])) throw new Error(`config: thresholds missing or invalid (${k})`);
  }
  // Keys are interpolated into SQL JSON paths below, so allow only lower_snake_case.
  for (const k of [...cfg.aspects, ...themes.map((x) => x.key)]) {
    if (!/^[a-z][a-z0-9_]*$/.test(k)) throw new Error(`config: label key ${k} must be lower_snake_case`);
  }
  return cfg;
}

// demoConfig: used instead of config when the database was seeded by `seed-demo`
// (meta.demo = 1), so demo data is shown with its fictional products and a banner.
function createApp({ dbPath, config, demoConfig }) {
  if (!fs.existsSync(dbPath)) {
    throw new Error(`database not found at ${dbPath}. Run "make demo" (synthetic data) or `
      + '"python -m sentiment run" first.');
  }
  const db = new Database(dbPath);
  db.pragma('busy_timeout = 5000');
  const hasMeta = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").get();
  const demo = Boolean(hasMeta && db.prepare("SELECT 1 FROM meta WHERE key = 'demo' AND value = '1'").get());
  const cfg = demo && demoConfig ? demoConfig : config;
  const ASPECTS = cfg.aspects;
  const VALID_DIMENSIONS = ['overall', ...ASPECTS];
  const THEMES = cfg.themes.map((t) => t.key);
  const { aboutMin, confMin, labelMin } = cfg;

  db.exec(`
    CREATE TABLE IF NOT EXISTS annotations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_date TEXT NOT NULL,
      competitor_name TEXT DEFAULT NULL,
      label TEXT NOT NULL,
      description TEXT DEFAULT '',
      url TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_annotations_date ON annotations(event_date);
  `);

  const app = express();
  app.use(express.static(path.join(__dirname, 'public')));
  app.use(express.json());

  // One row per counted mention, with its bucketed sentiment. (The UI's API calls the
  // product "competitor"; the primary product is just the one given the hero card.)
  const COUNTED = `
    WITH c AS (
      SELECT m.product AS competitor, m.aspect, m.aspects, m.themes, m.source,
             m.sentiment AS score, m.item_id, i.kind, i.thread_id, i.created_utc, t.subreddit,
             CASE WHEN m.sentiment < 1.5 THEN 'negative'
                  WHEN m.sentiment > 2.5 THEN 'positive' ELSE 'neutral' END AS val
      FROM mentions m
      JOIN items i ON i.reddit_id = m.item_id
      JOIN threads t ON t.reddit_id = i.thread_id
      WHERE m.judged_at IS NOT NULL AND m.about >= ${aboutMin} AND m.sentiment_conf >= ${confMin}
    )`;

  // d is whitelisted by callers (VALID_DIMENSIONS / THEMES, validated at config load).
  const dimCond = (d) => (d === 'overall' ? '1=1'
    : `(CASE WHEN c.aspects IS NULL THEN c.aspect = '${d}'
            ELSE json_extract(c.aspects, '$.${d}') >= ${labelMin} END)`);
  const themeCond = (t) => `json_extract(c.themes, '$.${t}') >= ${labelMin}`;

  function dimValues(row) {
    const asp = row.aspects ? JSON.parse(row.aspects) : null;
    const out = { overall: row.val };
    for (const a of ASPECTS) {
      const on = asp ? asp[a] >= labelMin : row.aspect === a;
      out[a] = on ? row.val : 'not_applicable';
    }
    return out;
  }
  const themeList = (row) => {
    const th = row.themes ? JSON.parse(row.themes) : {};
    return THEMES.filter((t) => th[t] >= labelMin);
  };

  const overviewCols = VALID_DIMENSIONS.map((d) => `
      SUM(CASE WHEN ${dimCond(d)} THEN 1 ELSE 0 END) AS ${d}_applicable,
      SUM(CASE WHEN ${dimCond(d)} AND c.val = 'positive' THEN 1 ELSE 0 END) AS ${d}_positive,
      SUM(CASE WHEN ${dimCond(d)} AND c.val = 'negative' THEN 1 ELSE 0 END) AS ${d}_negative,
      SUM(CASE WHEN ${dimCond(d)} AND c.val = 'neutral' THEN 1 ELSE 0 END) AS ${d}_neutral`).join(',');

  const stmtOverview = db.prepare(`${COUNTED}
    -- total_posts = counted mentions (posts + comments), so "N of total" never exceeds 100%.
    SELECT c.competitor AS competitor_name, COUNT(*) AS total_posts, ${overviewCols}
    FROM c WHERE c.created_utc >= ?
    GROUP BY c.competitor ORDER BY total_posts DESC`);

  const stmtTrends = {};
  for (const d of VALID_DIMENSIONS) {
    stmtTrends[d] = db.prepare(`${COUNTED}
      SELECT date(c.created_utc, 'unixepoch') AS day,
        SUM(c.val = 'positive') AS positive, SUM(c.val = 'negative') AS negative,
        SUM(c.val = 'neutral') AS neutral, 0 AS mixed, COUNT(*) AS total
      FROM c WHERE c.competitor = ? AND ${dimCond(d)} AND c.created_utc >= ?
      GROUP BY day ORDER BY day ASC`);
  }

  const stmtItemMentions = db.prepare(`${COUNTED}
    SELECT c.competitor, c.aspect, c.aspects, c.themes, c.source, c.val FROM c WHERE c.item_id = ?`);
  const stmtThreadComments = db.prepare(`${COUNTED}
    SELECT DISTINCT i.rowid AS rid, i.reddit_id, i.text AS body, i.score,
           datetime(i.created_utc, 'unixepoch') AS posted_at
    FROM c JOIN items i ON i.reddit_id = c.item_id
    WHERE c.thread_id = ? AND c.kind = 'comment'
    ORDER BY i.score DESC LIMIT 25`);
  const stmtPostItem = db.prepare('SELECT text, score FROM items WHERE reddit_id = ?');

  const sentimentsFor = (itemId) => stmtItemMentions.all(itemId).map((s) => ({
    competitor: s.competitor, ...dimValues(s), themes: themeList(s),
    inferred: s.source === 'inferred',
  }));

  const stmtThemes = {};
  for (const t of THEMES) {
    stmtThemes[t] = db.prepare(`${COUNTED}
      SELECT COUNT(*) AS total, SUM(c.val = 'positive') AS positive,
             SUM(c.val = 'negative') AS negative, SUM(c.val = 'neutral') AS neutral
      FROM c WHERE c.competitor = ? AND ${themeCond(t)} AND c.created_utc >= ?`);
  }

  const fail = (res, where, err) => {
    console.error(`${where} error:`, err.message);
    res.status(503).json({ error: 'Database temporarily unavailable' });
  };

  app.get('/api/config', (req, res) => {
    res.json({
      title: cfg.title, primary_product: cfg.primaryProduct, products: cfg.products,
      dimensions: VALID_DIMENSIONS, themes: cfg.themes, min_n: cfg.minN, demo,
    });
  });

  app.get('/api/themes', (req, res) => {
    try {
      const { competitor, since } = req.query;
      if (!competitor) return res.status(400).json({ error: 'competitor parameter is required' });
      res.json(THEMES.map((t) => ({ theme: t, ...stmtThemes[t].get(competitor, parseInt(since) || 0) }))
        .filter((r) => r.total > 0).sort((a, b) => b.total - a.total));
    } catch (err) { fail(res, 'Themes', err); }
  });

  app.get('/api/overview', (req, res) => {
    try {
      const since = parseInt(req.query.since) || 0;
      res.json(stmtOverview.all(since).map((row) => {
        const dimensions = {};
        for (const d of VALID_DIMENSIONS) {
          dimensions[d] = {
            applicable: row[`${d}_applicable`], positive: row[`${d}_positive`],
            negative: row[`${d}_negative`], neutral: row[`${d}_neutral`], mixed: 0,
          };
        }
        return { competitor: row.competitor_name, total_posts: row.total_posts, dimensions };
      }));
    } catch (err) { fail(res, 'Overview', err); }
  });

  app.get('/api/trends', (req, res) => {
    try {
      const { competitor, dimension, since } = req.query;
      if (!competitor) return res.status(400).json({ error: 'competitor parameter is required' });
      if (!VALID_DIMENSIONS.includes(dimension)) {
        return res.status(400).json({ error: `Invalid dimension. Must be one of: ${VALID_DIMENSIONS.join(', ')}` });
      }
      res.json(stmtTrends[dimension].all(competitor, parseInt(since) || 0));
    } catch (err) { fail(res, 'Trends', err); }
  });

  // Threads containing a counted mention that matches the filters, newest first, each
  // with its post-level sentiments and its counted comments (+ their sentiments).
  app.get('/api/posts', (req, res) => {
    try {
      const { competitor, subreddit, since, until, dimension, sentiment, theme } = req.query;
      const cond = [];
      const params = [];
      if (competitor) { cond.push('c.competitor = ?'); params.push(competitor); }
      if (subreddit) { cond.push('c.subreddit = ?'); params.push(subreddit); }
      if (since) { cond.push('c.created_utc >= ?'); params.push(parseInt(since)); }
      if (until) { cond.push('c.created_utc <= ?'); params.push(parseInt(until)); }
      if (dimension && sentiment) {
        if (!VALID_DIMENSIONS.includes(dimension)) {
          return res.status(400).json({ error: `Invalid dimension. Must be one of: ${VALID_DIMENSIONS.join(', ')}` });
        }
        cond.push(`${dimCond(dimension)} AND c.val = ?`);
        params.push(sentiment);
      }
      if (theme) {
        if (!THEMES.includes(theme)) return res.status(400).json({ error: 'Invalid theme' });
        cond.push(themeCond(theme));
      }
      const where = cond.length ? 'WHERE ' + cond.join(' AND ') : '';
      const threads = db.prepare(`${COUNTED}
        SELECT t.rowid AS rid, t.reddit_id, t.subreddit, t.title, t.url, t.num_comments,
               datetime(t.created_utc, 'unixepoch') AS posted_at, MAX(c.created_utc) AS last_ts
        FROM c JOIN threads t ON t.reddit_id = c.thread_id ${where}
        GROUP BY t.reddit_id ORDER BY last_ts DESC LIMIT 100`).all(...params);

      res.json(threads.map((t) => {
        const post = stmtPostItem.get(t.reddit_id);
        const selftext = post ? post.text.replace(t.title, '').trim() : '';
        const competitor_sentiments = post ? sentimentsFor(t.reddit_id) : [];
        const comments = stmtThreadComments.all(t.reddit_id).map((cm) => ({
          id: cm.rid, reddit_id: cm.reddit_id, body: cm.body, author: '',
          score: cm.score, posted_at: cm.posted_at,
          competitor_sentiments: sentimentsFor(cm.reddit_id),
        }));
        const names = new Set([...competitor_sentiments, ...comments.flatMap((c) => c.competitor_sentiments)]
          .map((s) => s.competitor));
        return {
          // numeric id: the UI interpolates it into inline onclick handlers
          id: t.rid, reddit_id: t.reddit_id, subreddit: t.subreddit, title: t.title,
          selftext, url: t.url, author: '', score: post ? post.score : 0,
          num_comments: t.num_comments, posted_at: t.posted_at,
          competitors: [...names].join(','), competitor_sentiments, comments,
        };
      }));
    } catch (err) { fail(res, 'Posts', err); }
  });

  app.get('/api/competitors', (req, res) => {
    try {
      res.json(db.prepare(`${COUNTED} SELECT DISTINCT competitor FROM c ORDER BY competitor`).all()
        .map((r) => r.competitor));
    } catch (err) { fail(res, 'Competitors', err); }
  });

  app.get('/api/subreddits', (req, res) => {
    try {
      res.json(db.prepare(`${COUNTED} SELECT DISTINCT subreddit FROM c ORDER BY subreddit`).all()
        .map((r) => r.subreddit));
    } catch (err) { fail(res, 'Subreddits', err); }
  });

  app.get('/api/stats', (req, res) => {
    try {
      const q = (sql) => db.prepare(sql).get();
      const range = q('SELECT MIN(created_utc) AS min_ts, MAX(created_utc) AS max_ts FROM threads');
      res.json({
        total_posts: q('SELECT COUNT(*) AS n FROM threads').n,
        classified_posts: q(`${COUNTED} SELECT COUNT(DISTINCT thread_id) AS n FROM c`).n,
        counted_mentions: q(`${COUNTED} SELECT COUNT(*) AS n FROM c`).n,
        date_range: {
          earliest: range.min_ts ? new Date(range.min_ts * 1000).toISOString() : null,
          latest: range.max_ts ? new Date(range.max_ts * 1000).toISOString() : null,
        },
        competitor_count: q(`${COUNTED} SELECT COUNT(DISTINCT competitor) AS n FROM c`).n,
        subreddit_count: q(`${COUNTED} SELECT COUNT(DISTINCT subreddit) AS n FROM c`).n,
        last_collection_utc: range.max_ts,
      });
    } catch (err) { fail(res, 'Stats', err); }
  });

  app.get('/api/annotations', (req, res) => {
    try {
      const { competitor, since } = req.query;
      let sql = 'SELECT * FROM annotations WHERE 1=1';
      const params = [];
      if (competitor) { sql += ' AND (competitor_name = ? OR competitor_name IS NULL)'; params.push(competitor); }
      if (since) { sql += " AND event_date >= date(?, 'unixepoch')"; params.push(parseInt(since)); }
      res.json(db.prepare(sql + ' ORDER BY event_date DESC').all(...params));
    } catch (err) { fail(res, 'Annotations', err); }
  });

  app.post('/api/annotations', (req, res) => {
    try {
      const { event_date, competitor_name, label, description, url } = req.body || {};
      if (!event_date || !label) return res.status(400).json({ error: 'event_date and label are required' });
      if (!/^\d{4}-\d{2}-\d{2}$/.test(event_date)) {
        return res.status(400).json({ error: 'event_date must be in YYYY-MM-DD format' });
      }
      const r = db.prepare('INSERT INTO annotations (event_date, competitor_name, label, description, url) VALUES (?, ?, ?, ?, ?)')
        .run(event_date, competitor_name || null, label, description || '', url || '');
      res.status(201).json({ id: r.lastInsertRowid });
    } catch (err) { fail(res, 'Annotation insert', err); }
  });

  app.delete('/api/annotations/:id', (req, res) => {
    try {
      const r = db.prepare('DELETE FROM annotations WHERE id = ?').run(parseInt(req.params.id));
      if (r.changes === 0) return res.status(404).json({ error: 'Not found' });
      res.json({ ok: true });
    } catch (err) { fail(res, 'Annotation delete', err); }
  });

  app.locals.db = db;
  app.locals.demo = demo;
  return app;
}

module.exports = { createApp, loadConfig };

if (require.main === module) {
  const dataDir = process.env.DATA_DIR ? path.resolve(process.env.DATA_DIR) : path.join(ROOT, 'data');
  const dbPath = process.env.DB_PATH || path.join(dataDir, 'sentiment.db');
  const configPath = process.env.CONFIG_PATH || path.join(ROOT, 'config.yaml');
  const demoConfigPath = process.env.DEMO_CONFIG_PATH || path.join(ROOT, 'config.demo.yaml');
  const host = process.env.HOST || '127.0.0.1';   // local only unless you choose otherwise
  const port = parseInt(process.env.PORT, 10) || 3852;
  let app;
  try {
    app = createApp({ dbPath, config: loadConfig(configPath), demoConfig: loadConfig(demoConfigPath) });
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
  const close = () => { try { app.locals.db.close(); } catch (e) { /* ignore */ } process.exit(0); };
  process.on('SIGINT', close);
  process.on('SIGTERM', close);
  app.listen(port, host, () => console.log(`Sentiment dashboard on http://${host}:${port}`
    + (app.locals.demo ? ' (synthetic demo data)' : '')));
}
