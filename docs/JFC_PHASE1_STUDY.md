# JFC PHASE1 STUDY

First study is to compare the quality of the JFC Phase I output `STRATEGY.md` using different LLMs. 
They are all executed with the same prompt and command: 

```bash
uv run hepagent jfc run -n higgs2tautau -t measurement -p tasks/jfc-cms-h2tautau.md --model "amsc:gpt-5.4-mini" --max-turns 50 --max-iterations 5 --yolo
```

Each experiment uses the following information:
- model-name: "ollama:gemma-4-31B-it"
- output-folder: "analyses/results/higgs2tautau/gemma-4-31B-it"

All results are saved at: `analyses/results/higgs2tautau`
Here is a list of LLMs to compare and their cost per 1M tokens (input / output):

| Name | Cost per 1M tokens (input / output) | Context window size | Max output tokens | 
| --- | --- | --- | --- |
| "ollama:gemma-4-31B-it" | $0 / $0 | 262,144 | Up to remaining context |
| "amsc:claude-sonnet-4-6" | $3 / $15 | 1,000,000 | 64,000 |
| "amsc:gpt-5.4-high" | $2.5 / $15 | 1,050,000 | 128,000 |
| "amsc:gpt-5.4-mini" | $0.75 / $4.5 | 272,000 | 128,000 |
| "cborg:gemini-3-flash-high" | $0.25 / $3 | 1,048,576 | 65,535 |
