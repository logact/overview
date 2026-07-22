# overview

### we manage and build our life around the github.  
  
  
workflow

1. brainstorm ,input your any idea in a issue with different labels //todo (more labels)
2. organize these issue and dispatch to different project. schedule them in the github pojects roadmap

##   
feedback eval function

1. set the milestone for a phase measurable goal 
  1. set daily milestone
  2. set 3 days milestone
  3. set  week milestone
  4. set month milestone
  5. set 6 month milestone
  6. set yearly milestone
2. at each milestone we should check our OKR and summarize

## scheduled work summarization

`.github/workflows/summarize.yml` runs every 6 hours (00:00/06:00/12:00/18:00 UTC, plus manual
`workflow_dispatch`). It queries issues and PRs updated in the last 6 hours (including comments),
compresses them (image/base64 stripping, code-fence and excerpt truncation, size caps), summarizes
them with the Kimi/Moonshot API, and creates an issue labeled `summarization` with the report.

Requires the repo secret `MOONSHOT_API_KEY`. Reports never summarize previous `summarization`
issues, and no issue is created when there is no activity in the window. 





