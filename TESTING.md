# End-to-end test report

The README was followed step by step as a new user would, against freshly created resources.
This file records what worked, what broke, and the changes made on the `testing` branch.

## Environment

| Item | Value |
| --- | --- |
| Date | 2026-10-07 |
| OS / shell | Windows, PowerShell 7.6.6 |
| Python | 3.14.0 (setup scripts and agent dependencies both install and import cleanly) |
| Azure CLI | 2.77.0 |
| azd | 1.33.0, `microsoft.foundry` 1.0.0-beta.2, `azure.ai.agents` 1.0.0-beta.13 |
| Region | Sweden Central |
| Resources | New resource group with a Foundry account + project, `gpt-4.1` (GlobalStandard), Azure AI Search Basic |
| SharePoint | One team site with a BYOD/MFA onboarding PDF in `Shared Documents` |
| Licence | Test user has a Microsoft 365 Copilot licence |

## Results

| Step | Result | Notes |
| --- | --- | --- |
| Prerequisites: roles | Pass | Search Service Contributor, Search Index Data Reader and Foundry User assigned to the test user |
| 1. venv + `pip install` + `.env` | Pass | |
| 2a. `create_knowledge_base.py` | Pass after a wait | Failed with `ConnectionResetError 10054` until the new Search service finished provisioning (see finding 2) |
| 2b. `create_connection.py` | Pass | |
| 2c. `create_toolbox.py` | Pass | |
| 2. Re-run all three scripts | Pass | Idempotent: `createdThisRun: false`, no overwrite |
| Direct KB retrieve as the user | Pass | Returned SharePoint content, so the Copilot Retrieval API path works |
| 3. `azd ai agent init` | **Fail as written** | `fatal: pathspec '*' did not match any files` (see finding 1). Passed from a folder outside the repo |
| 3. `azd env set` + `azd up` | Pass | Code deploy finished in about 3m20s |
| 4. `azd ai agent invoke` (MFA question) | Pass | Accurate answer with a SharePoint citation link |
| Negative question (not in SharePoint) | Pass | "The available sources do not contain information about..." |
| Publish to Teams, `-WhatIf` | Pass | |
| Publish to Teams, `-PublishScope Shared` | **Fail as written** | `InvalidBotData: The bot name is already registered` (see finding 3). Passed with a unique `-BotName` |
| Verify user A / user B in Teams | Not run | Needs two real users in Teams; see "Still to do" |

## Findings and changes

### Blocking issues (fixed)

1. **`azd ai agent init` fails when run as the README describes.** Step 1 leaves you in `setup/`,
   so `./deploy` is created inside the repo. The repo's `.gitignore` excludes `deploy/`, so the
   `git add` run by `azd init` matches nothing and fails with `pathspec '*' did not match any files`.
   - *Change:* README step 3 now uses `$HOME/sharepoint-kb-deploy` and explains why the folder must
     be outside the repo.

2. **A new Search service isn't ready when ARM says it is.** ARM reported `Succeeded`, but the
   data plane stayed in `status: provisioning` for about 10 minutes and reset TLS connections
   (`ConnectionResetError 10054`). The scripts also need RBAC auth on Search, which is off by
   default (`apiKeyOnly`), and the README didn't mention it.
   - *Change:* README prerequisites now say to enable role-based access
     (`--auth-options aadOrApiKey`) and to wait for `status` = `running`.

3. **The Teams publish script's default bot name collides globally.** Azure Bot names are unique
   across Azure. The default was the agent name (`sharepoint-kb-agent`), which someone else had
   already registered. The script also hid the cause, printing only
   `Bot deployment failed for 'sharepoint-kb-agent'.`
   - *Change:* the default bot name now adds a stable 6-character hash of the project endpoint
     (for example `sharepoint-kb-agent-0426ea`), so re-runs still update the same bot.
   - *Change:* on a failed deployment, the script now adds the ARM operation error message to the
     exception.
   - *Change:* README note about `-BotName`. The Bicep parameter description now says "globally
     unique across Azure" instead of "within the subscription".

### Consistency fixes

4. **Search API versions didn't match.** `create_knowledge_base.py` defaulted to
   `2026-05-01-preview` while `create_connection.py`, `create_toolbox.py` and the current docs use
   `2026-08-01-preview`. `.env.example` set `2026-05-01-preview` for all three.
   - *Change:* both the default and `.env.example` now use `2026-08-01-preview`. Tested:
     creating the KB and calling MCP `tools/list` both succeed with it.
   - *Upgrade note:* if a connection was already created with the old version,
     `create_connection.py` reports a `target` mismatch. Keep `SEARCH_API_VERSION` at the old
     value, or delete and recreate the connection and toolbox.

5. **SharePoint path guidance.** People naturally copy the browser URL, such as
   `.../Shared%20Documents/Forms/AllItems.aspx`. That's a list view, not a content path, so the
   KQL `Path:` filter scopes nothing.
   - *Change:* `.env.example` now says to use a site or library URL instead.

### Recommendations (not changed)

6. **Dockerfile vs runtime.** `azure.yaml` deploys as code with `runtime: python_3_13`, and
   `azd up` doesn't use the Dockerfile. The Dockerfile still pins `python:3.12-slim`. Either align
   it to 3.13 or note that it's only for local container runs.
7. **No placeholder validation.** Running a script with the unedited `.env` fails with a DNS error
   for `%3Csearch_service%3E.search.windows.net`. A check in `required_env()` for values
   containing `<` would give a clearer message.
8. **azd extension status.** `azd ext list` shows `azure.ai.agents` and `azure.ai.projects` as
   *Incompatible* with azd 1.33.0, though `init`/`up`/`invoke` all worked. Consider recommending
   `azd extension update --all` and a tested azd version.
9. **`--new-session` reused the same conversation and session IDs** on two consecutive invokes
   (azd behaviour, not this repo). It's worth checking if you rely on isolated sessions.
10. **Public-network projects.** The README says the script is for private-network projects, but
    it also works without `-UseM365PublicEndpoint` on a public project. That's useful for
    automation or CI where the portal button isn't an option.
11. **Testing the KB directly.** With `minimal` reasoning effort, the retrieve API rejects
    `messages` and needs `intents`. A short troubleshooting snippet would help users check
    SharePoint access before deploying the agent:

    ```powershell
    $tok = az account get-access-token --resource https://search.azure.com --query accessToken -o tsv
    $body = '{"intents":[{"type":"semantic","search":"onboarding"}],"knowledgeSourceParams":[{"knowledgeSourceName":"ks-sharepoint","kind":"remoteSharePoint","includeReferences":true}]}'
    Invoke-RestMethod -Method Post -ContentType application/json -Body $body `
      -Uri "https://<SEARCH_SERVICE>.search.windows.net/knowledgebases/kb-sharepoint/retrieve?api-version=2026-08-01-preview" `
      -Headers @{ Authorization = "Bearer $tok"; "x-ms-query-source-authorization" = $tok }
    ```

## Still to do (manual)

- **Permission trimming in Teams.** The agent is published with `Shared` scope. Use a document
  that user A can open and user B can't, and confirm the README's "Verify" table. Shared scope
  only shows the agent to the publisher, so the second user needs `Tenant` scope (admin
  approval) or must be added to the Shared audience.
- **Clean up** when finished: delete the test resource group, which removes the Foundry account,
  Search service and bot. Remove the Teams app from the Microsoft 365 admin center if it was
  approved.
