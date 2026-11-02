// ─────────────────────────────────────────────────────────────
//  EDGE Paper Trading Engine v1.0
//  Strategy: Becker Maker Framework
//  - Focus on high-gap categories (taker flow emocional)
//  - Prefer selling YES / buying NO (Optimism Tax)
//  - Quarter-Kelly sizing on virtual bankroll
// ─────────────────────────────────────────────────────────────

const fs   = require("fs");
const path = require("path");

// ── TELEGRAM CONFIG ─────────────────────────────────────────
const TELEGRAM = {
  enabled: Boolean(process.env.TELEGRAM_BOT_TOKEN),
  bot_token: process.env.TELEGRAM_BOT_TOKEN || "",
  chat_id: null,  // Se auto-detecta con /api/telegram/setup
  chat_id_file: path.join(__dirname, "telegram_chat_id.json"),
};

// Load saved chat_id
try {
  if (fs.existsSync(TELEGRAM.chat_id_file)) {
    TELEGRAM.chat_id = JSON.parse(fs.readFileSync(TELEGRAM.chat_id_file, "utf8")).chat_id;
    console.log(`[TELEGRAM] Chat ID loaded: ${TELEGRAM.chat_id}`);
  }
} catch (e) { /* ignore */ }

// Send message to Telegram channel
async function sendTelegram(text) {
  if (!TELEGRAM.enabled || !TELEGRAM.chat_id) return;
  try {
    const url = `https://api.telegram.org/bot${TELEGRAM.bot_token}/sendMessage`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: TELEGRAM.chat_id,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
    if (!resp.ok) console.log(`[TELEGRAM] Error: ${resp.status}`);
  } catch (e) {
    console.log(`[TELEGRAM] Send failed: ${e.message}`);
  }
}

// Format trade notification for Telegram
function formatTradeNotification(trade, journal) {
  const closed = journal.trades.filter(t => t.status === "closed");
  const wins = closed.filter(t => t.resolution === "WIN");
  const losses = closed.filter(t => t.resolution === "LOSS");
  const wr = closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) : "0";
  const grossW = wins.reduce((s, t) => s + t.pnl, 0);
  const grossL = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
  const pf = grossL > 0 ? (grossW / grossL).toFixed(2) : "∞";

  const icon = trade.resolution === "WIN" ? "✅" : "❌";
  const pnlSign = trade.pnl > 0 ? "+" : "";

  return [
    `${icon} <b>${trade.resolution}</b> ${pnlSign}$${trade.pnl.toFixed(2)}`,
    ``,
    `${trade.direction} @ ${trade.entry_price}¢ · ${trade.category}`,
    `${trade.question.slice(0, 60)}`,
    ``,
    `📊 ${wins.length}W/${losses.length}L · WR ${wr}% · PF ${pf}`,
    `💰 Bankroll: $${journal.bankroll.toFixed(2)} (${((journal.bankroll - 10000) / 100).toFixed(2)}%)`,
  ].join("\n");
}

// Setup endpoint helper — discovers chat_id from recent messages
async function telegramSetup() {
  try {
    const url = `https://api.telegram.org/bot${TELEGRAM.bot_token}/getUpdates`;
    const resp = await fetch(url);
    const data = await resp.json();
    
    if (!data.ok || !data.result || data.result.length === 0) {
      return { error: "No updates found. Send any message in the channel first, then retry." };
    }

    // Find channel/group chat_ids
    const chats = new Map();
    for (const update of data.result) {
      const msg = update.message || update.channel_post || update.my_chat_member?.chat;
      if (msg && msg.chat) {
        chats.set(msg.chat.id, {
          id: msg.chat.id,
          title: msg.chat.title || msg.chat.first_name || "Unknown",
          type: msg.chat.type,
        });
      }
    }

    if (chats.size === 0) {
      return { error: "No chats found. Make the bot admin in the channel and send a message first." };
    }

    // Auto-select if only one channel/group
    const chatList = [...chats.values()];
    const channels = chatList.filter(c => c.type === "channel" || c.type === "supergroup" || c.type === "group");
    
    if (channels.length === 1) {
      TELEGRAM.chat_id = channels[0].id;
      fs.writeFileSync(TELEGRAM.chat_id_file, JSON.stringify({ chat_id: TELEGRAM.chat_id }));
      return { success: true, chat_id: TELEGRAM.chat_id, title: channels[0].title };
    }

    return { chats: chatList, message: "Multiple chats found. Set chat_id manually." };
  } catch (e) {
    return { error: e.message };
  }
}
const fetch = require("node-fetch");

const POLY_API = "https://gamma-api.polymarket.com";
const DATA_FILE = path.join(__dirname, "journal.json");

// ── BECKER CATEGORY GAPS ─────────────────────────────────────
const BECKER_GAPS = {
  "Sports": 2.23, "Politics": 1.02, "Crypto": 2.69,
  "Finance": 0.17, "Weather": 2.57, "Entertainment": 4.79,
  "Media": 7.28, "World Events": 7.32, "Other": 1.5,
};

// ── STRATEGY CONFIG ──────────────────────────────────────────
const CONFIG = {
  initial_bankroll: 10000,       // $10K virtual
  kelly_fraction: 0.20,          // Kelly 0.20 (subido de 0.15) — más modulación por edge
  min_gap: 1.5,                  // Min Becker gap to trade (pp)
  min_volume_24h: 5000,          // Min $5K 24h volume
  max_open_positions: 25,        // 25 posiciones — capturar máximo de oportunidades
  max_per_market: 1,             // 1 position per market
  min_edge_pp: 2,                // Min estimated edge (pp) — captura más operaciones
  scan_interval_min: 10,         // Scan every 10 min
  spread_cents: 2,               // Maker spread (± cents)
  longshot_threshold: 25,        // Below this → strong longshot bias
  max_horizon_days: 60,          // Max días hasta resolución (bajado de 90)
  max_exposure_pct: 1.5,         // Max exposure 150% del bankroll — leverage moderado
  max_position_pct: 0.04,        // Cap por trade = 4% del bankroll (bajado de 5%)
  // Categorías bloqueadas — Sports demostró ser net loser (-$1,250 en 119 trades)
  // Becker gap teórico no se traduce a edge real en deportes (mercados muy eficientes)
  blocked_categories: ["Sports"],
  // Bloqueo de mercados con rangos numéricos (Elon tweets, etc.) — trampa estadística
  // Parecen "muy probables" por el rango estrecho pero la varianza real es enorme
  block_numeric_ranges: true,
  // Price ranges where Becker shows most mispricing
  // Sports tiene zona más amigable (75-87¢) porque favoritos extremos son eficientes
  // World Events / Media / otros usan zona alta (88-97¢) donde Becker tiene más edge
  sweet_spots_default: [
    { min: 88, max: 97, bias: "sell_no", edge_mult: 1.2 },  // zona alta segura
    { min: 3, max: 12, bias: "sell_yes", edge_mult: 1.2 },  // longshot (buy NO)
  ],
  sweet_spots_by_category: {
    // Sports: zona media-alta donde hay home bias pero ratio loss/win es manejable
    // Evita favoritos extremos donde el ratio es 10:1 (NO 91¢ pierde $91 gana $9)
    Sports: [
      { min: 75, max: 87, bias: "sell_no", edge_mult: 1.0 },
      { min: 13, max: 25, bias: "sell_yes", edge_mult: 1.0 },
    ],
  },
  // ── FILTRO DE CONCENTRACIÓN POR TEMA ──────────────────────────
  // El patrón que destruyó la V1: 4 posiciones simultáneas sobre Irán = -$3,481.
  // El cap por trade (4%) protege contra un trade malo, pero NO contra que muchas
  // posiciones del mismo tema caigan juntas por una sola noticia (riesgo de correlación).
  // Límite: máximo N posiciones abiertas que compartan un cluster temático.
  max_per_cluster: 5,            // máx posiciones abiertas por tema correlacionado
  theme_clusters: {
    iran:      ["iran", "iranian", "tehran", "ceasefire", "hormuz", "uranium", "kharg", "khamenei", "irgc"],
    israel:    ["israel", "gaza", "hamas", "hezbollah", "netanyahu", "idf", "west bank"],
    russia:    ["russia", "russian", "ukraine", "putin", "zelensky", "kyiv", "moscow", "donbas"],
    us_elect:  ["trump", "biden", "harris", "election", "gop", "democrat", "republican", "senate", "congress"],
    china:     ["china", "chinese", "taiwan", "xi jinping", "beijing", "pla"],
    fed:       ["fed", "fomc", "powell", "interest rate", "rate cut", "rate hike"],
    crypto:    ["bitcoin", "btc", "ethereum", "eth", "solana", "crypto"],
  },
};

// ── HELPER: detectar el cluster temático de un mercado ──
// Devuelve la clave del cluster (iran, russia…) cuyo keyword aparezca en la pregunta,
// o null si no matchea ninguno. Se usa para el filtro de concentración por tema.
function themeOf(question) {
  const q = (question || "").toLowerCase();
  for (const [cluster, keywords] of Object.entries(CONFIG.theme_clusters || {})) {
    if (keywords.some(kw => q.includes(kw))) return cluster;
  }
  return null;
}

// Cuenta cuántas posiciones ABIERTAS pertenecen ya a un cluster temático dado.
function openInCluster(journal, cluster) {
  if (!cluster) return 0;
  return journal.trades.filter(
    t => t.status === "open" && themeOf(t.question) === cluster
  ).length;
}

// ── HELPER: detect numeric range markets (Elon tweets, etc.) ──
// Filtra mercados con rangos estrechos que parecen probables pero tienen alta varianza
function hasNumericRange(question) {
  if (!question) return false;
  const q = question.toLowerCase();
  // Remove dates first to avoid false positives (2026-04-12, etc.)
  const cleaned = q.replace(/\b(20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-20\d{2})\b/g, "");
  // Pattern 1: "X-Y <countable>" where countable is tweets/posts/goals/etc
  if (/\b\d+\s*-\s*\d+\s+(tweet|post|point|goal|run|hit|score|view|like|follower|subscriber|comment|reply)/.test(cleaned)) return true;
  // Pattern 2: "post/tweet X-Y"
  if (/(post|tweet|score|goal)\s+\d+\s*-\s*\d+/.test(cleaned)) return true;
  // Pattern 3: "between X and Y"
  if (/between\s+\d+\s+and\s+\d+\s+(tweet|post|point|goal|run|hit|score|view|like|follower)/.test(cleaned)) return true;
  // Pattern 4: "from X to Y" followed by a countable noun
  if (/from\s+\d+\s+to\s+\d+\s+(tweet|post|point|goal|run|hit|score|view|like|follower)/.test(cleaned)) return true;
  // Pattern 5: explicit "<N" or ">N" with countable (e.g. "post <40 tweets")
  if (/[<>]\s*\d+\s+(tweet|post|point|goal|run|hit|view|like|follower)/.test(cleaned)) return true;
  return false;
}

// ── DATA STORE ───────────────────────────────────────────────
function loadJournal() {
  try {
    if (fs.existsSync(DATA_FILE)) {
      return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
    }
  } catch (e) {
    console.error("[PAPER] Error loading journal:", e.message);
  }
  return {
    config: { ...CONFIG },
    bankroll: CONFIG.initial_bankroll,
    trades: [],
    log: [],
    stats: { total: 0, wins: 0, losses: 0, open: 0, total_pnl: 0, peak_bankroll: CONFIG.initial_bankroll, max_drawdown: 0 },
    created_at: new Date().toISOString(),
  };
}

function saveJournal(journal) {
  try {
    fs.writeFileSync(DATA_FILE, JSON.stringify(journal, null, 2));
  } catch (e) {
    console.error("[PAPER] Error saving journal:", e.message);
  }
}

// ── CLASSIFY CATEGORY ────────────────────────────────────────
// Checks: question, tags, description, event titles/series
function classifyCategory(market) {
  // Build a big searchable string from all available fields
  const parts = [];
  if (market.question) parts.push(market.question);
  if (market.tags) parts.push(Array.isArray(market.tags) ? market.tags.join(" ") : String(market.tags));
  if (market.description) parts.push(market.description.slice(0, 300));
  if (market.events) {
    for (const ev of market.events) {
      if (ev.title) parts.push(ev.title);
      if (ev.series) {
        for (const s of ev.series) {
          if (s.title) parts.push(s.title);
          if (s.ticker) parts.push(s.ticker);
        }
      }
    }
  }
  if (market.slug) parts.push(market.slug);

  const t = parts.join(" ").toLowerCase();

  // ── SPORTS — check first (highest volume category)
  if (t.includes("nba") || t.includes("nfl") || t.includes("nhl") || t.includes("mlb") ||
      t.includes("soccer") || t.includes("football") || t.includes("tennis") ||
      t.includes("f1 ") || t.includes("formula 1") || t.includes("ufc") ||
      t.includes("boxing") || t.includes("mls") || t.includes("ligue 1") ||
      t.includes("serie a") || t.includes("la liga") || t.includes("premier league") ||
      t.includes("bundesliga") || t.includes("champions league") ||
      t.includes("wimbledon") || t.includes("masters tournament") ||
      t.includes("bnp paribas") || t.includes("atp") || t.includes("wta") ||
      t.includes(" vs. ") || t.includes(" vs ") ||
      t.includes("o/u ") || t.includes("moneyline") ||
      t.includes("win on 2026") || t.includes("win on 2025") ||
      t.includes("world baseball") || t.includes("cricket") || t.includes("odi series") ||
      t.includes("counter-strike") || t.includes("dota") || t.includes("lol:") ||
      t.includes("esport") || t.includes("esl pro") || t.includes("pgl ")) return "Sports";

  // ── CRYPTO
  if (t.includes("bitcoin") || t.includes("btc") || t.includes("ethereum") || t.includes("eth ") ||
      t.includes("solana") || t.includes("crypto") || t.includes("price of bitcoin")) return "Crypto";

  // ── FINANCE
  if (t.includes("fed ") || t.includes("fomc") || t.includes("interest rate") ||
      t.includes("fed chair") || t.includes("crude oil") || t.includes("s&p") ||
      t.includes("nasdaq") || t.includes("stock market") || t.includes("gdp") ||
      t.includes("inflation") || t.includes("treasury") || t.includes("tariff")) return "Finance";

  // ── WORLD EVENTS (geopolitics, wars, international)
  if (t.includes("iran") || t.includes("israel") || t.includes("ukraine") || t.includes("russia") ||
      t.includes("strike") || t.includes("ceasefire") || t.includes("regime fall") ||
      t.includes("invade") || t.includes("netanyahu") || t.includes("khamenei") ||
      t.includes("greenland") || t.includes("strait of hormuz") || t.includes("kharg island") ||
      t.includes("forces enter") || t.includes("war ") || t.includes("nuclear")) return "World Events";

  // ── POLITICS (elections, nominations, domestic policy)
  if (t.includes("president") || t.includes("nomination") || t.includes("republican") ||
      t.includes("democrat") || t.includes("election") || t.includes("senate") ||
      t.includes("congress") || t.includes("governor") || t.includes("parliament") ||
      t.includes("trump") && !t.includes("strike") || t.includes("biden") ||
      t.includes("nominee") || t.includes("cabinet") || t.includes("confirmed as")) return "Politics";

  // ── MEDIA (social media, influencers)
  if (t.includes("elon musk") || t.includes("tweet") || t.includes("twitter") ||
      t.includes("tiktok") || t.includes("youtube") || t.includes("post ") && t.includes("from march")) return "Media";

  // ── ENTERTAINMENT
  if (t.includes("oscar") || t.includes("grammy") || t.includes("emmy") ||
      t.includes("movie") || t.includes("celebrity") || t.includes("award") ||
      t.includes("box office") || t.includes("album")) return "Entertainment";

  // ── WEATHER
  if (t.includes("weather") || t.includes("hurricane") || t.includes("temperature") ||
      t.includes("earthquake")) return "Weather";

  return "Other";
}

// ── HELPER: extract end date from market (Polymarket has multiple field names) ──
function getMarketEndDate(market) {
  if (!market) return null;
  const candidates = [
    market.endDate,
    market.end_date_iso,
    market.endDateIso,
    market.end_date,
    market.gameStartTime,
    market.acceptingOrdersTimestamp,
  ];
  for (const c of candidates) {
    if (c) {
      const d = new Date(c);
      if (!isNaN(d.getTime())) return d.toISOString();
    }
  }
  // Try events array (some markets nest end date in events)
  if (market.events && market.events.length > 0) {
    for (const ev of market.events) {
      const evCandidates = [ev.endDate, ev.end_date_iso, ev.endDateIso, ev.end_date];
      for (const c of evCandidates) {
        if (c) {
          const d = new Date(c);
          if (!isNaN(d.getTime())) return d.toISOString();
        }
      }
    }
  }
  return null;
}

// ── STRATEGY LOGIC ───────────────────────────────────────────
function evaluateMarket(market) {
  const prices = market.outcomePrices ? JSON.parse(market.outcomePrices) : null;
  if (!prices || prices.length < 1) return null;

  const yesPrice = Math.round(parseFloat(prices[0]) * 100);
  const noPrice = 100 - yesPrice;
  const vol24 = parseFloat(market.volume24hr || 0);
  const cat = classifyCategory(market);
  const gap = BECKER_GAPS[cat] || 1.5;

  // Filters
  if (CONFIG.blocked_categories && CONFIG.blocked_categories.includes(cat)) return null;
  if (CONFIG.block_numeric_ranges && hasNumericRange(market.question)) return null;
  if (gap < CONFIG.min_gap) return null;
  if (vol24 < CONFIG.min_volume_24h) return null;
  if (yesPrice <= 1 || yesPrice >= 99) return null;

  // Horizon filter: skip markets that resolve too far in the future
  const endDateStr = getMarketEndDate(market);
  if (endDateStr) {
    const endDate = new Date(endDateStr);
    const now = new Date();
    const daysUntil = (endDate - now) / (1000 * 60 * 60 * 24);
    if (daysUntil > CONFIG.max_horizon_days) return null;
  }

  // Find sweet spot — usar zonas específicas por categoría si existen
  const spots = CONFIG.sweet_spots_by_category[cat] || CONFIG.sweet_spots_default;
  const spot = spots.find(s => yesPrice >= s.min && yesPrice <= s.max);
  if (!spot) return null;

  // Estimate edge based on Becker gap + longshot bias
  let estimatedEdge = gap * spot.edge_mult;

  // Longshot bonus
  if (yesPrice <= CONFIG.longshot_threshold) {
    estimatedEdge += (CONFIG.longshot_threshold - yesPrice) * 0.1;
  }
  if (yesPrice >= 100 - CONFIG.longshot_threshold) {
    estimatedEdge += (yesPrice - (100 - CONFIG.longshot_threshold)) * 0.1;
  }

  if (estimatedEdge < CONFIG.min_edge_pp) return null;

  // Determine direction (Becker: NO outperforms at 69/99 price levels)
  let direction, entryPrice, reason;

  if (spot.bias === "sell_yes") {
    // Act as maker selling YES → we buy NO
    direction = "NO";
    entryPrice = noPrice + CONFIG.spread_cents; // Maker gets slightly better price
    reason = `Maker selling YES at ${yesPrice}¢ (buying NO at ${entryPrice}¢). Cat: ${cat} gap ${gap}pp. Longshot zone.`;
  } else if (spot.bias === "sell_no") {
    direction = "YES";
    entryPrice = yesPrice - CONFIG.spread_cents;
    reason = `Maker selling NO at ${noPrice}¢ (buying YES at ${entryPrice}¢). Cat: ${cat} gap ${gap}pp.`;
  } else {
    // Neutral: still prefer NO (Becker YES/NO asymmetry)
    direction = "NO";
    entryPrice = noPrice;
    reason = `Neutral zone, defaulting to NO (Becker YES/NO asymmetry). Cat: ${cat} gap ${gap}pp.`;
  }

  entryPrice = Math.max(1, Math.min(99, entryPrice));

  // Kelly sizing
  const impliedProb = entryPrice / 100;
  const estimatedTrueProb = impliedProb + (estimatedEdge / 100);
  const boundedProb = Math.max(0.02, Math.min(0.98, estimatedTrueProb));
  const b = (1 / (entryPrice / 100)) - 1;
  const kelly = Math.max(0, (boundedProb * b - (1 - boundedProb)) / b);
  const qKelly = kelly * CONFIG.kelly_fraction;

  return {
    market_id: market.id,
    question: market.question,
    category: cat,
    becker_gap: gap,
    yes_price: yesPrice,
    no_price: noPrice,
    volume_24h: vol24,
    direction,
    entry_price: entryPrice,
    estimated_edge: Math.round(estimatedEdge * 100) / 100,
    kelly_fraction: Math.round(qKelly * 10000) / 10000,
    reason,
    spot_bias: spot.bias,
    end_date: getMarketEndDate(market),
  };
}

// ── SCAN & TRADE ─────────────────────────────────────────────
async function scanAndTrade(journal) {
  const timestamp = new Date().toISOString();
  console.log(`[PAPER] Scanning markets at ${timestamp}...`);

  journal.log.push({ ts: timestamp, type: "scan", msg: "Starting market scan" });

  try {
    // Fetch active markets
    const resp = await fetch(`${POLY_API}/markets?limit=80&active=true&closed=false&order=volume24hr&ascending=false`, {
      headers: { "Accept": "application/json" }, timeout: 15000,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const markets = await resp.json();

    // Get existing open position market IDs
    const openIds = new Set(journal.trades.filter(t => t.status === "open").map(t => t.market_id));

    let evaluated = 0, entered = 0;

    // Calculate current total exposure
    const currentExposure = journal.trades
      .filter(t => t.status === "open")
      .reduce((sum, t) => sum + (parseFloat(t.position_size) || 0), 0);

    for (const m of markets) {
      if (openIds.size + entered >= CONFIG.max_open_positions) break;
      if (openIds.has(m.id)) continue;

      // Check exposure limit before evaluating
      const openExposure = currentExposure + journal.trades
        .filter(t => t.status === "open" && !openIds.has(t.market_id))
        .reduce((sum, t) => sum + (parseFloat(t.position_size) || 0), 0);
      if (currentExposure > journal.bankroll * CONFIG.max_exposure_pct) {
        if (entered === 0) {
          journal.log.push({ ts: timestamp, type: "limit", msg: `Exposure limit reached: $${currentExposure.toFixed(0)} > ${(CONFIG.max_exposure_pct*100).toFixed(0)}% of $${journal.bankroll.toFixed(0)}. No new trades.` });
        }
        break;
      }

      const signal = evaluateMarket(m);
      if (!signal) { evaluated++; continue; }

      // ── FILTRO DE CONCENTRACIÓN POR TEMA ──
      // Rechaza la entrada si ya hay max_per_cluster posiciones abiertas del mismo tema.
      // Protege contra correlación: el patrón "14 posiciones sobre Irán" que amenazó la V2.3.
      const cluster = themeOf(signal.question);
      if (cluster) {
        const yaAbiertas = openInCluster(journal, cluster);
        if (yaAbiertas >= CONFIG.max_per_cluster) {
          journal.log.push({
            ts: timestamp, type: "cluster_block",
            msg: `BLOQUEADO por concentración: '${cluster}' ya tiene ${yaAbiertas} posiciones abiertas (máx ${CONFIG.max_per_cluster}). ${signal.question.slice(0, 50)}...`,
          });
          evaluated++;
          continue;
        }
      }

      // Calculate position size with dynamic percentage cap
      const kellySize = journal.bankroll * signal.kelly_fraction;
      const capSize = journal.bankroll * CONFIG.max_position_pct;
      let positionSize = Math.round(Math.min(kellySize, capSize) * 100) / 100;
      if (positionSize < 1) continue;
      const shares = Math.round(positionSize / (signal.entry_price / 100));

      const trade = {
        id: `T${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
        market_id: signal.market_id,
        question: signal.question,
        category: signal.category,
        becker_gap: signal.becker_gap,
        direction: signal.direction,
        entry_price: signal.entry_price,
        yes_price_at_entry: signal.yes_price,
        estimated_edge: signal.estimated_edge,
        kelly_fraction: signal.kelly_fraction,
        position_size: positionSize,
        shares: shares,
        reason: signal.reason,
        spot_bias: signal.spot_bias,
        status: "open",
        opened_at: timestamp,
        end_date: signal.end_date,
        closed_at: null,
        resolution: null,
        pnl: null,
        pnl_pct: null,
      };

      journal.trades.push(trade);
      journal.stats.open++;
      journal.stats.total++;
      entered++;

      journal.log.push({
        ts: timestamp, type: "entry",
        msg: `OPEN ${signal.direction} | ${signal.question.slice(0, 60)}... | ${signal.entry_price}¢ | $${positionSize} | Edge ~${signal.estimated_edge}pp | ${signal.category}`,
      });

      console.log(`[PAPER] ✓ ENTRY: ${signal.direction} ${signal.question.slice(0, 50)}... @ ${signal.entry_price}¢ ($${positionSize})`);
    }

    // Check for resolved markets
    const resolved = await checkResolutions(journal);

    journal.log.push({
      ts: timestamp, type: "scan_complete",
      msg: `Scan done. Evaluated: ${markets.length}, New entries: ${entered}, Resolved: ${resolved}. Open: ${journal.stats.open}. Bankroll: $${journal.bankroll.toFixed(2)}`,
    });

    console.log(`[PAPER] Scan complete: ${entered} new, ${resolved} resolved, ${journal.stats.open} open, bankroll $${journal.bankroll.toFixed(2)}`);
  } catch (e) {
    journal.log.push({ ts: timestamp, type: "error", msg: `Scan error: ${e.message}` });
    console.error(`[PAPER] ✗ Scan error: ${e.message}`);
  }

  saveJournal(journal);
  return journal;
}

// ── CHECK RESOLUTIONS ────────────────────────────────────────
async function checkResolutions(journal) {
  let resolved = 0;
  const openTrades = journal.trades.filter(t => t.status === "open");

  for (const trade of openTrades) {
    try {
      const resp = await fetch(`${POLY_API}/markets/${trade.market_id}`, {
        headers: { "Accept": "application/json" }, timeout: 10000,
      });
      if (!resp.ok) continue;
      const m = await resp.json();

      // Backfill end_date if missing (for legacy trades opened before this fix)
      if (!trade.end_date) {
        const backfilled = getMarketEndDate(m);
        if (backfilled) trade.end_date = backfilled;
      }

      if (m.closed || m.resolved) {
        const outcomes = m.outcomePrices ? JSON.parse(m.outcomePrices) : null;
        if (!outcomes) continue;

        const yesPrice = parseFloat(outcomes[0]);
        const noPrice = parseFloat(outcomes[1] || (1 - yesPrice));

        // Method 1: Price at extremes (clear resolution)
        const yesResolvedByPrice = yesPrice >= 0.95;
        const noResolvedByPrice = yesPrice <= 0.05;

        // Method 2: Market explicitly closed/resolved — use price direction
        const marketDone = m.closed === true || m.resolved === true;

        // Method 3: Past end date — market should have resolved
        let pastEndDate = false;
        const endStr = getMarketEndDate(m);
        if (endStr) {
          const end = new Date(endStr);
          const now = new Date();
          pastEndDate = now > end;
        }

        // Skip if market is not done by any method
        if (!yesResolvedByPrice && !noResolvedByPrice && !marketDone && !pastEndDate) continue;

        // Determine winner: use price > 0.5 as YES, otherwise NO
        let yesWon;
        if (yesResolvedByPrice) yesWon = true;
        else if (noResolvedByPrice) yesWon = false;
        else if (marketDone || pastEndDate) {
          // Market is closed but price not at extremes — infer from price
          yesWon = yesPrice > 0.5;
        } else {
          continue;
        }

        // Determine P&L
        let won;
        if (trade.direction === "YES") {
          won = yesWon;
        } else {
          won = !yesWon;
        }

        const pnl = won
          ? trade.shares * ((100 - trade.entry_price) / 100)
          : -(trade.shares * (trade.entry_price / 100));
        const pnlPct = (pnl / trade.position_size) * 100;

        trade.status = "closed";
        trade.closed_at = new Date().toISOString();
        trade.resolution = won ? "WIN" : "LOSS";
        trade.pnl = Math.round(pnl * 100) / 100;
        trade.pnl_pct = Math.round(pnlPct * 100) / 100;

        journal.bankroll += pnl;
        journal.stats.open--;
        if (won) journal.stats.wins++;
        else journal.stats.losses++;
        journal.stats.total_pnl += pnl;

        // Track drawdown
        if (journal.bankroll > journal.stats.peak_bankroll) {
          journal.stats.peak_bankroll = journal.bankroll;
        }
        const dd = ((journal.stats.peak_bankroll - journal.bankroll) / journal.stats.peak_bankroll) * 100;
        if (dd > journal.stats.max_drawdown) journal.stats.max_drawdown = Math.round(dd * 100) / 100;

        journal.log.push({
          ts: trade.closed_at, type: won ? "win" : "loss",
          msg: `${trade.resolution} ${trade.direction} | ${trade.question.slice(0, 50)}... | P&L: $${trade.pnl > 0 ? "+" : ""}${trade.pnl.toFixed(2)} (${trade.pnl_pct > 0 ? "+" : ""}${trade.pnl_pct.toFixed(1)}%) | Bankroll: $${journal.bankroll.toFixed(2)}`,
        });

        console.log(`[PAPER] ${trade.resolution}: ${trade.direction} ${trade.question.slice(0, 40)}... P&L $${trade.pnl.toFixed(2)}`);
        
        // Send Telegram notification
        sendTelegram(formatTradeNotification(trade, journal));
        
        resolved++;
      }
    } catch (e) {
      // Silent - market might not be accessible
    }
  }
  return resolved;
}

// ── STATS CALCULATOR ─────────────────────────────────────────
function calcStats(journal) {
  const closed = journal.trades.filter(t => t.status === "closed");
  const open = journal.trades.filter(t => t.status === "open");
  const wins = closed.filter(t => t.resolution === "WIN");
  const losses = closed.filter(t => t.resolution === "LOSS");

  const winRate = closed.length > 0 ? (wins.length / closed.length * 100) : 0;
  const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 0;
  const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + t.pnl, 0) / losses.length : 0;
  const totalPnl = closed.reduce((s, t) => s + (t.pnl || 0), 0);
  const avgReturn = closed.length > 0 ? closed.reduce((s, t) => s + (t.pnl_pct || 0), 0) / closed.length : 0;

  // Equity curve
  let equity = CONFIG.initial_bankroll;
  const equityCurve = [{ date: journal.created_at, equity }];
  closed.sort((a, b) => (a.closed_at || "").localeCompare(b.closed_at || ""));
  for (const t of closed) {
    equity += t.pnl || 0;
    equityCurve.push({ date: t.closed_at, equity: Math.round(equity * 100) / 100 });
  }

  // By category
  const byCat = {};
  for (const t of closed) {
    if (!byCat[t.category]) byCat[t.category] = { trades: 0, wins: 0, pnl: 0 };
    byCat[t.category].trades++;
    if (t.resolution === "WIN") byCat[t.category].wins++;
    byCat[t.category].pnl += t.pnl || 0;
  }

  // Capital metrics
  const capitalCommitted = open.reduce((sum, t) => sum + (t.position_size || 0), 0);
  const capitalFloating = journal.bankroll - capitalCommitted;
  const exposurePct = journal.bankroll > 0 ? (capitalCommitted / journal.bankroll) * 100 : 0;

  return {
    total_trades: journal.trades.length,
    closed: closed.length,
    open: open.length,
    wins: wins.length,
    losses: losses.length,
    win_rate: Math.round(winRate * 100) / 100,
    avg_win: Math.round(avgWin * 100) / 100,
    avg_loss: Math.round(avgLoss * 100) / 100,
    total_pnl: Math.round(totalPnl * 100) / 100,
    avg_return_pct: Math.round(avgReturn * 100) / 100,
    bankroll: Math.round(journal.bankroll * 100) / 100,
    initial_bankroll: CONFIG.initial_bankroll,
    bankroll_change_pct: Math.round(((journal.bankroll - CONFIG.initial_bankroll) / CONFIG.initial_bankroll) * 10000) / 100,
    max_drawdown: journal.stats.max_drawdown,
    peak_bankroll: Math.round(journal.stats.peak_bankroll * 100) / 100,
    capital_committed: Math.round(capitalCommitted * 100) / 100,
    capital_floating: Math.round(capitalFloating * 100) / 100,
    exposure_pct: Math.round(exposurePct * 100) / 100,
    equity_curve: equityCurve,
    by_category: byCat,
    config: CONFIG,
  };
}

module.exports = {
  loadJournal,
  saveJournal,
  scanAndTrade,
  checkResolutions,
  calcStats,
  evaluateMarket,
  classifyCategory,
  telegramSetup,
  sendTelegram,
  CONFIG,
};
