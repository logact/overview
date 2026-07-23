speed up the dev of task

a lesson from the pr ci error 

[https://github.com/logact/OPC/pull/3/changes/7bc0fabda6a5d44149acc85609dda309b758a63c](https://github.com/logact/OPC/pull/3/changes/7bc0fabda6a5d44149acc85609dda309b758a63c)  
this pr work for a  long time   
cause 2 questions 

1. the CI consume too long time   cause the
  1. ios build for long time about 17min
  2. network set faild
2. the agent work for a too long time cause the token  consume too much ,as we statics we spent more than 90 token in it.
  1. it read too many log from the github CI log

improve 

1. for every task we should draft a budget (time and token) ,if the task is over the budget the human should concern it and dive into the task and steer the agent.
2. let the CI fail fast to let the agent know
3. use cache skill to speed up the CI test
4. when something is stuck we should be brave to deep into the task.
5. Break the push→wait→paste-CI-log loop. That pattern cost ~90M tokens. Instead: reproduce Maestro failures locally

  before pushing, and pull CI logs via gh run view --log-failed inside the session rather than pasting 2k-char log

  dumps — the agent can fetch them itself, and they don't get re-sent on every turn.
6.  Narrow CI iteration scope earlier. Your last prompt in that session ("先不要去跑 Maestro e2e，先验证 ATS") was the

        right move — doing that 5 iterations earlier would have saved most of the 28.6M.

  

   