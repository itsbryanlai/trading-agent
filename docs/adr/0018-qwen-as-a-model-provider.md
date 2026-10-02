# 0018. Qwen is an approved model provider alongside Anthropic

Status: accepted

## Context

The constitution's Technology section names the `anthropic` SDK for every LLM
agent. The owner is cost-conscious about the agents and chose Qwen3.7-Plus:
- for the Opportunistic Identifier, on 2026-09-30, with the decision deferred to
  its own feature;
- for Research, on 2026-10-01, as the default, with an optional switch to a
  Sonnet model.

A second model vendor is a new external dependency and a new credential category,
so it needs an ADR (Constitution V). It also needs a constitution amendment.

According to QwenCloud's model page (https://www.qwencloud.com/models/qwen3.7-plus,
read 2026-10-01):
- **Access:** Qwen3.7-Plus is served through an OpenAI-compatible endpoint
  (`https://maas.qwencloudapi.com/compatible-mode/v1`), authenticated with a
  DashScope (Alibaba Cloud Model Studio) API key.
- **List price:** $0.40 per million input tokens and $1.60 per million output.
  The page also shows a 20% promotional discount, which is not relied on here.
- **Features:** structured outputs and function calling are supported.

## Decision

1. **Approved model providers are Anthropic and Qwen.** Anthropic is used through
   the `anthropic` SDK. Qwen is used through QwenCloud's OpenAI-compatible API.
   Adding a third provider needs its own ADR.
2. **Each agent's provider and model are configuration**, version-controlled and
   changed only through code review. Choosing between approved providers needs no
   further ADR. Each agent's feature records its choice:
   - **Research:** defaults to `qwen3.7-plus`, with Anthropic's Sonnet as an
     optional switch.
   - **The Opportunistic Identifier:** expected to use Qwen, confirmed in its own
     feature.
   - **The PM and the Assistant:** not decided here.
3. **No automatic failover between providers.** A failed model call follows the
   agent's own failure rule; for Research, a `no_action` report saying why.
   Failing over would silently send the agent's prompts to a second vendor and
   change its cost.
4. **Credentials follow [0015](0015-orchestrator-starts-agents-with-their-own-credentials.md):**
   `<PREFIX>_DASHSCOPE_API_KEY` for Qwen and `<PREFIX>_ANTHROPIC_API_KEY` for
   Anthropic, for example `RESEARCH_DASHSCOPE_API_KEY`. Only the configured
   provider's key needs to be set. Neither key can reach the broker.
5. **Output is validated by code, whatever the provider.** An agent never writes
   model output to the database without checking it against its schema first.

## Alternatives considered

- **Anthropic only, Haiku-class for cost.** Not chosen: the owner compared prices
  and preferred Qwen. The earlier estimate for the Opportunistic Identifier was
  about $1.60 a month on Qwen against $4.40 on Haiku 4.5.
- **Qwen only.** Not chosen: the owner wants Sonnet available as a quality
  fallback, without another ADR.
- **Qwen through its Anthropic-compatible endpoint**, so every agent uses the
  `anthropic` SDK. Not ruled out, but not confirmed on the model page. The client
  library is a plan-level choice in the first feature that calls Qwen.

## Consequences

- **Prompts reach Alibaba's servers** when an agent uses Qwen. For Research, a
  prompt holds public news and its own earlier reports: no credentials and no
  portfolio data. An agent whose prompts would carry portfolio state, such as the
  PM, weighs this in its own feature before choosing Qwen.
- **A new runtime dependency:** an OpenAI-compatible client, or plain HTTP,
  decided in the plan.
- **Two providers' behaviour to test:** each agent's tests use a fake model
  client and never call a provider (`tests/conftest.py` blocks the network).
- **Cost and quality comparisons become possible** per agent through
  configuration, with the journal's attribution showing the difference over time.
- **The constitution's Technology section is amended** (v1.1.0) to name both
  providers and point here.
