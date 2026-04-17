"""Example 2: LangChain agent with trust verification.

An AI agent that can browse, find products, and verify merchants
before making purchases. The agent decides on its own when to
call the trust verification tool.

Requirements:
    pip install langchain-openai opentrusttoken

Set your OpenAI API key:
    export OPENAI_API_KEY=sk-...

Usage: python3 langchain_agent.py
"""

import sys
import os
sys.path.insert(0, "../sdk/python")

# Check for API key
if not os.environ.get("OPENAI_API_KEY"):
    print("This demo requires an OpenAI API key.")
    print("Set it with: export OPENAI_API_KEY=sk-...")
    print()
    print("Running in simulation mode instead...\n")
    SIMULATION = True
else:
    SIMULATION = False

from opentrusttoken import check


def simulate_agent():
    """Simulate what a LangChain agent does, without requiring OpenAI."""

    scenarios = [
        {
            "task": "Buy a USB-C cable from amazon.com for under $15",
            "domain": "amazon.com",
            "amount": 12.99,
        },
        {
            "task": "Purchase premium widgets from scosi.com",
            "domain": "scosi.com",
            "amount": 49.99,
        },
        {
            "task": "Order a new laptop from totally-fake-xyz123.com",
            "domain": "totally-fake-xyz123.com",
            "amount": 899.00,
        },
        {
            "task": "Subscribe to cloudflare.com Pro plan",
            "domain": "cloudflare.com",
            "amount": 25.00,
        },
    ]

    for scenario in scenarios:
        print("=" * 60)
        print(f"TASK: {scenario['task']}")
        print("=" * 60)
        print()
        print(f"Agent: I need to buy from {scenario['domain']}.")
        print(f"Agent: Let me verify this merchant first...")
        print()

        result = check(scenario["domain"])

        print(f"[OTT Tool Response]")
        print(f"  {result.domain}: Score {result.trust_score}/100 ({result.recommendation})")
        print(f"  {result.reasoning}")
        if result.flags:
            print(f"  Flags: {', '.join(result.flags)}")
        print(f"  Jurisdiction: {result.jurisdiction.country} ({result.jurisdiction.cross_border_risk} risk)")
        print()

        # Agent decision
        amount = scenario["amount"]
        if result.is_blocked:
            print(f"Agent: This merchant failed trust verification (score: {result.trust_score}).")
            print(f"Agent: I cannot proceed with this purchase. Searching for alternatives...")
        elif result.has_critical_flags:
            print(f"Agent: CRITICAL security flags detected. Refusing this merchant.")
        elif result.is_risky:
            if amount > 200:
                print(f"Agent: This merchant scored {result.trust_score} (CAUTION).")
                print(f"Agent: The amount ${amount:.2f} exceeds my CAUTION limit of $200.")
                print(f"Agent: I need your approval before proceeding. Confirm? [simulated: no]")
            else:
                print(f"Agent: This merchant scored {result.trust_score} (CAUTION).")
                print(f"Agent: Amount ${amount:.2f} is within my CAUTION limit.")
                print(f"Agent: Proceeding with purchase at {scenario['domain']}.")
        else:
            print(f"Agent: Merchant verified (score: {result.trust_score}, PROCEED).")
            print(f"Agent: Completing purchase of ${amount:.2f} at {scenario['domain']}.")

        print()
        print()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()
    print("The agent made autonomous trust decisions for each merchant:")
    print("  amazon.com          -> PROCEED, purchased")
    print("  scosi.com           -> PROCEED, purchased")
    print("  totally-fake...     -> DENY, refused")
    print("  cloudflare.com      -> PROCEED, purchased")
    print()
    print("No human intervention needed for trusted merchants.")
    print("Untrusted merchants were automatically refused.")
    print("CAUTION merchants had transaction limits applied.")


def run_real_agent():
    """Run an actual LangChain agent with the OTT tool."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain.agents import create_react_agent, AgentExecutor
        from langchain_core.prompts import PromptTemplate
        from opentrusttoken.integrations.langchain import OTTVerifyTool
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install with: pip install langchain-openai langchain")
        print("\nFalling back to simulation mode...\n")
        simulate_agent()
        return

    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    tools = [OTTVerifyTool()]

    template = """You are a purchasing agent. Before making any purchase,
you MUST verify the merchant using the verify_merchant_trust tool.

Based on the trust verification result:
- PROCEED (score 75+): Complete the purchase
- CAUTION (score 40-74): Only proceed if amount is under $200, otherwise ask for confirmation
- DENY (score 0-39): Refuse the purchase and explain why

You have access to these tools:
{tools}

Tool names: {tool_names}

Task: {input}

{agent_scratchpad}"""

    prompt = PromptTemplate.from_template(template)
    agent = create_react_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    tasks = [
        "Verify if stripe.com is trustworthy for a $500 payment",
        "Check if totally-fake-xyz123.com is safe to buy from",
        "I want to purchase a $25 subscription from cloudflare.com. Is it safe?",
    ]

    for task in tasks:
        print("\n" + "=" * 60)
        print(f"TASK: {task}")
        print("=" * 60)
        result = executor.invoke({"input": task})
        print(f"\nAgent's response: {result['output']}")


if __name__ == "__main__":
    if SIMULATION:
        simulate_agent()
    else:
        run_real_agent()
