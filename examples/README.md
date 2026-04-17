# OpenTrustToken Integration Examples

## Setup

```bash
# From the examples/ directory
pip install httpx

# For LangChain example (optional)
pip install langchain-openai langchain
export OPENAI_API_KEY=sk-...
```

## Examples

### 1. Basic Check (`basic_check.py`)

The simplest integration. Check a domain and make a payment decision.

```bash
python3 basic_check.py stripe.com 49.99
python3 basic_check.py totally-fake-xyz123.com 899.00
```

### 2. LangChain Agent (`langchain_agent.py`)

An AI agent that autonomously verifies merchants before purchasing.
Runs in simulation mode without an OpenAI key, or as a real agent with one.

```bash
# Simulation mode (no API key needed)
python3 langchain_agent.py

# Real agent mode
export OPENAI_API_KEY=sk-...
python3 langchain_agent.py
```

### 3. Multi-Merchant Comparison (`multi_merchant.py`)

Agent compares prices across merchants, filters out untrustworthy ones,
and recommends the best deal among trusted options.

```bash
python3 multi_merchant.py
```

### 4. Async Batch Checking (`async_batch.py`)

Check 10 domains concurrently. Shows how to verify a portfolio of
merchants at once.

```bash
python3 async_batch.py
```
