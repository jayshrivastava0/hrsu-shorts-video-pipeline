# HRSU Blog Automation - Project Guide

## 🎯 Project Vision

**Goal:** Generate qualified B2B leads for HRSU's calcium nitrate products by creating SEO-optimized technical blogs that attract decision-makers in the chemical supply chain.

**Strategy:** Multi-platform blog automation that:
1. Publishes high-quality technical content to Blogger (SEO-friendly)
2. Distributes to LinkedIn and Facebook to reach procurement professionals and executives
3. Targets regional and use-case-specific keywords that procurement managers search for
4. Builds brand trust through consistent, authoritative technical content

**Success Metric:** Procurement professionals finding HRSU through organic blog search → visiting blog → following link to main website → becoming qualified leads.

---

## 🎯 Target Audience & Content Strategy

### Primary Audience: Procurement Managers & Supply Chain Professionals
**Goal:** Technical Trust-Building

- **Content Approach:** Deep, technical blogs with:
  - Specific chemical properties and application data
  - ROI calculations and cost-benefit analysis
  - Industry compliance & standards (REACH, EPA, etc.)
  - Real implementation examples and case studies
  - Technical specifications and handling procedures

- **Keywords:** Problem-focused searches like:
  - "calcium nitrate wastewater treatment"
  - "concrete accelerator for cold weather"
  - "ANFO explosives manufacturing"
  - "water treatment H2S control"
  - Regional variations (e.g., "calcium nitrate in Australia mining")

- **Content Examples:**
  - How to optimize calcium nitrate dosage in water treatment
  - Comparison: calcium nitrate vs. other accelerators for concrete
  - Technical deep-dive on REACH compliance
  - Regional case studies (Australia mining, EU wastewater, etc.)

### Secondary Audience: Executives & Decision Makers
**Goal:** Strategic Value & Partnership Recognition

- **Content Approach:** Different angle focusing on:
  - Business impact and market trends
  - Supply chain resilience and sourcing strategies
  - ESG and sustainability initiatives
  - Geopolitical and trade considerations
  - Strategic partnerships and growth opportunities

- **Keywords:** Market-focused searches like:
  - "India-EU FTA impact on chemical imports"
  - "sustainable fertilizer sourcing"
  - "supply chain strategy for mining chemicals"
  - "ESG chemical manufacturing"

- **Content Examples:**
  - HRSU's sustainability journey (garden, steam reuse, solar)
  - Trade policy impacts on chemical sourcing
  - Market trends in calcium nitrate applications
  - Strategic insights into supply chain resilience

### Why Two Strategies?
Procurement managers need **technical proof** → blog ranks for technical queries → drives search traffic.
Executives need **strategic context** → reinforces decision to partner with HRSU → accelerates conversion.

---

## 🏗️ Technical Architecture

### Pipeline Stages

```
Topic Generation
    ↓
Content Generation (Using Claude 3.5 Sonnet via API)
    ↓
SEO Optimization (Keywords, meta tags, structured data)
    ↓
Quality Guardrails (Remove AI artifacts, verify accuracy)
    ↓
Blogger Publishing (Primary distribution, SEO-friendly)
    ↓
Social Distribution (style-arm round-robin, real API posting)
    ├─ LinkedIn (LinkedIn Posts API v2)
    └─ Facebook (Meta Graph API)
    ↓
History & Deduplication (Prevent duplicate topics)
    ↓
Scoring Pipeline (GA4 signals → quality score → variant attribution → bandit)
```

### Core Components

**Blog Generation (live production path):**
- `run_blog.bat` → `run_blog_scheduled.py` - Windows Task Scheduler entry point (randomly picks
  persona per run unless `--persona` is passed explicitly)
- `blog_agent_v3.py` - **The actual production orchestrator** (`blog_agent_v2.py` is legacy/unused)
- `topic_generator.py` - AI-driven topic generation with regional/use-case targeting
- `content_generator.py` - Generates full blog posts with citations
- `seo_optimizer.py` - Adds meta tags, structured data (JSON-LD), internal linking
- `quality_guardrails.py` - Removes AI metadata/artifacts, ensures accuracy

**Publishing (live production path):**
- **Blogger**: Native API integration (Google OAuth 2.0) - Primary channel for SEO
- **LinkedIn**: `linkedin_api.py` - LinkedIn Posts API v2 (native API, real credentials in `secrets.txt`)
- **Facebook**: `facebook_api.py` - Meta Graph API (native API, real credentials in `secrets.txt`)
- `social_scheduler.py` - Queues/executes social posts at region peak time (`queue_social_post` →
  `_execute_social_posts`). **This is the real live posting path.**
- `social_scheduler.py` also picks the next style arm via `social_agent/policy.py`'s
  `RoundRobinPolicy` (8 arms: tone × angle × cta), renders it via `social_agent/styles.py`, and
  records the real arm to `scoring/variants.py` for bandit attribution (see Subsystem #4/#5 below).

**Not in production (built, tested, but not wired to anything live):**
- `social_agent/main.py`, `social_browser_agent.py` - Playwright-based browser automation. This
  was an earlier design (see history below) and passed its own tests, but the real posting path
  ended up being the native APIs above. Kept in the repo but not invoked by the scheduled pipeline
  — don't assume this is what's running without checking `data/style_policy.json` for evidence of
  actual execution (see progress doc `2026-07-18-scoring-pipeline-status-and-bandit-blocker.md`).
- `social_agent/orchestrator.py` - a separate publish-all module, always records variant label
  `'base'`; not used by the live scheduler either.

**Tracking:**
- `blog_history.json` - Tracks all published posts, prevents topic duplication
- `social_post_log.json` - Records social platform posts
- `data/scoring.sqlite` (`variants` table) - Real style-arm attribution per post/platform, written
  by `social_scheduler.py` on every live post

**Configuration:**
- `config.py` - Central config (blog ID, regions, API keys, thresholds)

### Social posting history (why there are two systems in the repo)

Original plan used LinkedIn Share API and Meta Graph API, but hit API permission/credential
friction for a single personal account, so a Playwright-based fallback (`social_agent/`) was built
and thoroughly tested. In practice, the native-API path (`linkedin_api.py`/`facebook_api.py`,
driven by `social_scheduler.py`) is what ended up live in production — the Playwright path exists
in the repo but has never actually executed there. As of 2026-07-18, `social_scheduler.py` now
also drives the round-robin style-arm system (originally built for the Playwright path) so the
real live posts get style variation and bandit attribution too, without needing Playwright at all.

---

## 📋 Development Guidelines

### Code Organization

**Rule:** Keep it modular and testable.

- One responsibility per file (topic gen, content gen, SEO, etc.)
- Avoid monolithic orchestrators where possible
- Config via `config.py` (no hardcoded values)
- Logs go to both stdout and `.log` files

### When Adding a New Platform

If adding Instagram, TikTok, or other social:

1. Create `social_agent/instagram_agent.py` (follow LinkedIn/Facebook pattern)
2. Implement these methods:
   - `login()` - Browser login
   - `post_content(title, url, summary)` - Post the content
   - `get_post_url()` - Return link to published post
3. Add to `social_agent/orchestrator.py` dispatcher
4. Update `config.py` with platform credentials
5. Test with `--dry-run` first

### Content Quality Standards

✅ **Must have:**
- No AI preamble ("Here's a blog post about...") or metadata
- Properly cited sources (DuckDuckGo research with superscript [1], [2], etc.)
- Technical depth appropriate to audience
- Internal link to main website (footer CTA)
- Regional context where relevant
- Proper heading hierarchy (H1 → H2 → H3)

❌ **Must NOT have:**
- "As of my last update..." or "I don't have real-time data..."
- Hedging language ("possibly", "might", "could be")
- AI disclaimer text
- Generic disclaimers

### Testing Before Publishing

Always use `--dry-run` first — `blog_agent_v3.py` requires `--persona` explicitly:

```bash
# Review content/scoring without publishing
python blog_agent_v3.py --persona procurement --dry-run

# Social posting is queued automatically as part of run_pipeline (step 8) —
# there's no separate social dry-run entry point on the live path anymore.
```

---

## 🔄 Workflow: From Idea to Live Post

### Automated Mode (Daily/Scheduled — this is what Task Scheduler actually runs)
```
run_blog.bat → run_blog_scheduled.py → blog_agent_v3.py
  → Randomly picks persona (procurement/executive) unless --persona is passed
  → Selects underutilized topic/region, checks for duplicates
  → Generates content, scores it, publishes to Blogger
  → Queues social posts (social_scheduler.py) at region peak time
      → Picks next style arm (round-robin), posts to LinkedIn + Facebook via native APIs
      → Records the real arm to scoring/variants.py
  → Logs in blog_history.json
```

### Manual Mode
```
python blog_agent_v3.py --persona procurement --region usa
  1. Generates topic, content, SEO metadata
  2. Publishes to Blogger
  3. Queues social posts (same style-arm rotation as the scheduled path)
  4. Logs to blog_history.json
```

### Quality Checkpoint
Before any publish, verify:
- [ ] No preamble text ("Here's a blog post...")
- [ ] Sources properly cited with [1], [2], etc.
- [ ] Regional context included
- [ ] CTA links to hrsuindore.com
- [ ] Heading structure is clean
- [ ] No hedge language present

---

## 📊 Regional & Audience Targeting

### Supported Regions
| Region | Language | Primary Personas | Focus Use Cases |
|--------|----------|------------------|-----------------|
| **Australia** | en-AU | Mining procurement managers | ANFO, water treatment, dust suppression |
| **USA** | en-US | Industrial/agri procurement | Fertilizers, concrete, wastewater |
| **EU** | en-GB | Industrial procurement | REACH compliance, sustainability |
| **Germany** | de-DE | Industrial procurement | Beton, Abwasser, Industrie |
| **East Asia** | en-SG | Mixed | Manufacturing, agriculture |
| **Gulf** | en-AE | Mining/O&G procurement | Drilling fluids, mining chemicals |

### Use Case Categories
Each use case gets multiple blogs from different angles:

1. **Wastewater Treatment** - H₂S control, BOD, denitrification
2. **Concrete & Construction** - Set acceleration, cold weather, corrosion
3. **Mining** - ANFO, dust suppression, acid mine drainage
4. **Agriculture** - Fertigation, hydroponics, blending
5. **Oil & Gas** - Drilling fluids, well cementing
6. **Water Treatment** - Cooling towers, boiler water, RO

**Deduplication Logic:**
- Posts by category + subcategory + problem + solution
- Jaccard similarity threshold: 0.75 (configurable)
- Max 4 posts per category per 30 days
- Suggested alternatives shown when duplicate detected

---

## 🚀 Current Status & Roadmap

### ✅ Completed
- [x] Blog generation pipeline (Blogger API integration)
- [x] Topic generation with regional targeting
- [x] Content generation with citations
- [x] SEO optimization (meta tags, structured data)
- [x] Deduplication system
- [x] LinkedIn integration (native API, `linkedin_api.py`, live in production)
- [x] Facebook integration (native API, `facebook_api.py`, live in production)
- [x] Quality guardrails (remove AI artifacts)
- [x] Batch generation
- [x] Statistics & export
- [x] Lead-quality scoring pipeline (GA4 → SQLite → quality score, Subsystems #1-#3.5)
- [x] Style-arm round-robin (Subsystem #4) — wired into the real live posting path 2026-07-18
- [x] Offline bandit (Subsystem #5) — Thompson sampling + Li-et-al replay, now receiving real
  variant observations as of 2026-07-18 (previously blocked at zero — see
  `docs/superpowers/progress/2026-07-18-style-arm-wiring-fix-and-live-test.md`)

### 🔄 In Progress
- **Bandit go-live**: waiting on observation accumulation (readiness gate: all 8 arms ≥ 5 obs
  each) before any human review of switching from round-robin to bandit-driven arm selection
- **Sentiment Analysis**: Optional engagement optimization

### ⏳ Future Enhancements
- [ ] **Image Generation**: AI-generated featured images per blog
- [ ] **A/B Testing**: Multiple headline variations
- [ ] **Analytics Integration**: Google Analytics tracking per region
- [ ] **Lead Magnet Integration**: CTA buttons linking to downloadables
- [ ] **Email Newsletter**: Auto-generate email from blog posts
- [ ] **Video Content**: YouTube scripts or podcast conversion
- [ ] **Competitor Analysis**: Track competitor topics, identify gaps
- [ ] **Personalization**: Track reader behavior, recommend related posts

---

## 🛠️ Local Development

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Configure
# 1. Get Google Blogger API credentials
# 2. Download client_secrets.json
# 3. Update BLOG_ID in config.py
# 4. Set LinkedIn/Facebook credentials (see ACTION_PLAN.md)
```

### Common Commands
```bash
# Generate and publish (persona required, region optional/random)
python blog_agent_v3.py --persona procurement --region usa

# Dry run (generate + score, don't publish or post socially)
python blog_agent_v3.py --persona procurement --dry-run

# Random persona/region, as Task Scheduler runs it daily
python run_blog_scheduled.py

# Re-ingest GA4 signals + rescore + regenerate report
python -m scripts.ingest_ga4 --days 2
python -m scoring.rescore --all
python -m scoring.report --as-of <YYYY-MM-DD>
```

### Debugging
- Check `hrsu_blog_automation.log` / `cron.log` for the scheduled pipeline
- Review `blog_history.json` for publishing history
- Inspect `social_post_log.json` for social media attempts
- Inspect `data/style_policy.json` for round-robin arm counters, `data/scoring.sqlite`
  (`variants` table) for real per-post style attribution
- Test Ollama connectivity: `ollama list` should show `gemma3:4b`

---

## 📝 Contributing Guidelines

### Before Making Changes

1. **Understand the audience**: Know who this blog is for (procurement manager vs. executive)
2. **Check deduplication**: Ensure topic isn't too similar to existing posts
3. **Verify SEO**: Check keywords match regional/audience intent
4. **Test content quality**: Remove any AI artifacts, verify sources

### Making Changes

**For bug fixes:**
- Test with `--dry-run` first
- Check `blog_history.json` to ensure deduplication still works

**For new features:**
- Add to modular file (don't bloat `blog_agent_v3.py`)
- Update `config.py` with any new settings
- Add tests or integration examples
- Document in this CLAUDE.md

**For new platforms/destinations:**
- Follow the native-API pattern (`linkedin_api.py`/`facebook_api.py`): auth via `token_manager.py`,
  a `post_*(..., text: Optional[str] = None)` method that accepts a style-rendered override
- Wire it into `social_scheduler.py`'s `_execute_social_posts` (the real live path), including
  round-robin arm selection and `scoring.variants.record_variant()` attribution
- Implement dry-run mode
- The Playwright-based `social_agent/` pattern (login → post → verify) is not the live path —
  don't extend it without first confirming which path is actually production (see `social
  posting history` section above)

---

## 🎓 Key Insights for Claude

### Understanding the Business Model
- **Goal**: Drive procurement manager searches → blogs → main website
- **Content Strategy**: Technical for procurement, strategic for executives
- **Success**: When a procurement manager finds your blog while researching "calcium nitrate wastewater treatment" and converts to a qualified lead

### Understanding the Tech Choices
- **Blogger**: SEO-friendly, native API, perfect for organic search
- **LinkedIn/Facebook**: native APIs (`linkedin_api.py`/`facebook_api.py`) are what's actually live
  in production, driven by `social_scheduler.py`. A Playwright-based alternative (`social_agent/`)
  exists in the repo and is fully tested, but has never executed in production — don't assume it's
  what's running without checking `data/style_policy.json` for evidence of real execution.
- **Deduplication**: Essential to avoid topic overlap and maximize search coverage
- **Regional Targeting**: Each region has different procurement preferences and regulations

### When Helping
- Always think about the **procurement manager perspective**: Would this blog help them solve a problem?
- Check for **AI artifacts**: These kill trust with technical audiences
- Verify **citations**: Technical buyers need sources
- Consider **regional context**: Wastewater regulations differ between Australia and USA

---

## 📞 Quick Reference

**Main files:**
- `blog_agent_v3.py` - Start here for main logic (production orchestrator; `blog_agent_v2.py` is legacy)
- `social_scheduler.py` - Real live social posting path (round-robin arm + native APIs)
- `linkedin_api.py` / `facebook_api.py` - Native API clients used in production
- `scoring/` - GA4 → quality score → variant attribution → bandit pipeline
- `config.py` - All configuration
- `requirements.txt` - Dependencies

**Key workflows:**
- Generate & publish: `python blog_agent_v3.py --persona procurement --region usa`
- Dry run: `python blog_agent_v3.py --persona procurement --dry-run`
- As Task Scheduler runs it: `python run_blog_scheduled.py`

**Important data files:**
- `blog_history.json` - What's been published
- `social_post_log.json` - Social posting record
- `client_secrets.json` - Google API credentials (don't commit!)

---

**Last Updated:** 2026-04-19
**Status:** Pipeline live for Blogger; LinkedIn/Facebook using Playwright
**Next Phase:** Optimize for SEO rankings and procurement manager conversions
