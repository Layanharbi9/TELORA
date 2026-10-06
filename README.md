# TELORA

**Your needs. Your pieces. Your solution.**

TELORA is an AI-based telecom package design and recommendation system. You tell it what you need, how much of it you use and how much you want to spend, and it builds your plan from real packages and add-ons, like puzzle pieces that fit around you.


## 🚀 Live Demo

**[Open the live demo →](https://layanharbi9.github.io/TELORA/)**

Sign in as a **Customer** to design a plan, or as **Admin** to explore the operations console.

> The demo is a static build on GitHub Pages ([`gh-pages`](https://github.com/Layanharbi9/TELORA/tree/gh-pages) branch). It uses the same interface with data recorded from TELORA's FastAPI service and SQLite catalog, and it replays real agent runs, so it makes no OpenAI calls. To run the full system with live agents, see **How to run TELORA** below.

---

## The idea

Telecom operators sell predefined packages and add-ons, but no two customers are the same. People differ in what they need, how much of it they use, what matters most to them and how much they want to spend. TELORA does not ask the customer to find the right plan. It builds that plan around them.

## Why we built it: the problem

Customers usually know what they need, but not which combination of packages and add-ons fits:

- **Too many packages and add-ons.** Customers have to browse and compare offers by hand.
- **Trade-offs are hard to judge.** More benefits can mean a higher price or features that go unused.
- **Needs are not one-size-fits-all.** Each customer has different usage levels and priorities.
- **Budget adds another constraint.** Customers have to balance what matters most against what they can spend.

A poor fit has a real cost:

- In Saudi Arabia, average mobile data use is **53 GB per person per month** (CST, *Saudi Internet Report 2025*).
- **28%** of customers who switch operators say their plan did not meet their needs (New Metrics KSA survey).

## Our solution

TELORA uses two cooperating AI agents to turn a customer's needs into a personalized, validated plan:

1. **The Search Agent** looks through a catalog of real packages and add-ons and keeps the ones that match the customer's needs and budget.
2. **The Solution Design Agent** combines them into up to three solutions. Each solution is **one base package plus 0–2 add-ons**. The agent prices each one, compares their trade-offs and recommends the best fit.

Three rules keep TELORA trustworthy:

- **Grounded.** Every package, add-on, price and ID comes from the catalog. The AI never invents offers or prices.
- **Safety checks.** Before the customer sees a solution, plain code checks that:
  - every item exists in the catalog;
  - the solution has one package and at most two add-ons;
  - the price is correct;
  - the price is no more than 15% over the customer's budget.
- **The customer decides.** Nothing changes on the plan until the customer chooses a solution.

---

## How it works: the user experience

### Customer

1. **Sign in.** Choose *Customer* and pick a customer account.
2. **My Plan.** See your current base package, add-ons and monthly price.
3. **Design My Solution.** Build your puzzle in five steps:
   1. **Your needs:** choose your **top 3 priorities** from nine needs and place them in order, most important first. The needs are Data, Calls, Gaming, Streaming, Social Media, Roaming, International Calls, Extra SIM and Data Sharing.
   2. **Set levels:** fill each piece to show how much you use it: *Low*, *Medium* or *High*.
   3. **Your budget:** choose how much you want to spend each month.
   4. **Your plan:** check your pieces and start the build.
   5. **Your solution:** watch TELORA work in real time (finding pieces, checking them, building solutions). Then compare the solutions, see which needs each one covers and how it compares with your budget and your current plan, and see which one is **recommended** and why.
4. **Select or cancel.** Press *Select* to apply a solution, and your plan is updated right away. Or cancel to keep your current plan.
5. **Requests.** See the history of every request and what changed.

**Example from our demo:** Sarah pays 195 SAR a month. She chose Data (High), Streaming (Low) and International Calls (Low) with a 400 SAR budget. TELORA recommended *Orbit Youth 15 + Intl 100* at 128.50 SAR. It covers all three needs and costs 66.50 SAR less than her current plan.

### Admin

The admin console shows how the whole system is doing:

- **Overview:** where requests stop, business impact and trust figures.
- **Requests:** what happened to each request, step by step.
- **Customers:** each customer's plan, usage and last request.
- **Agents:** how the Search Agent, the Solution Design Agent and the safety checks are performing.
- **Catalog:** which packages and add-ons the agents use most.

### Built with

HTML · CSS · JavaScript · FastAPI · LangGraph · OpenAI (`gpt-4o-mini`, `gpt-5.6-terra`) · SQLite · LangSmith

---

## Team

Built during the **Agentic AI Bootcamp** by **Saudi Digital Academy (SDA)** and **WeCloudData**.

**Team members**

- Layan Alharbi
- Farah Alhajjaji

**Supervisors**

- Hao Mai
- Mohamed Almoghalis

---

## How to run TELORA

**You need:**

- Python 3.10 or newer (3.12 recommended), from [python.org](https://www.python.org/downloads/).
- An OpenAI API key.

### Step 1: Add your API key

Create a file named `.env` in the project folder, next to `requirements.txt`, containing:

```dotenv
OPENAI_API_KEY=sk-your-key-here
```

Optional: to send traces to LangSmith, also add:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2-your-key-here
LANGSMITH_PROJECT=TELORA
```

### Step 2: Install and run

#### macOS (Terminal)

First time only:

```bash
cd ~/Desktop/Telora-2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

To run (every time):

```bash
cd ~/Desktop/Telora-2
source .venv/bin/activate
uvicorn backend.api.app:app --reload
```

#### Windows (PowerShell)

First time only:

```powershell
cd $HOME\Desktop\Telora-2
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

To run (every time):

```powershell
cd $HOME\Desktop\Telora-2
.venv\Scripts\Activate.ps1
uvicorn backend.api.app:app --reload
```

> If PowerShell says *"running scripts is disabled on this system"*, run this once and try again:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
>
> If you use Command Prompt (cmd), activate with `.venv\Scripts\activate.bat` instead.

### Step 3: Open the app

Go to **http://127.0.0.1:8000** in your browser. Choose **Customer** to build a plan or **Admin** to open the console.

To stop the server, press `Ctrl + C` in the terminal.

> **`uvicorn` not found?** The virtual environment is not active. Activate it (see above), or run `python -m uvicorn backend.api.app:app --reload` instead.
