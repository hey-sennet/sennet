"""What the recording pages play and ask for."""

# Replies in the shape agents give them: spoken versions of a few sentences, numbers said as
# words. Kokoro speaks them on the echo page.
REPLIES = [
    "All forty tests pass.",
    "Done. I fixed the failing import in the router, and the build is green again.",
    "I found two problems. The cache key ignores the user id, so people could see each other's "
    "settings, and the retry loop never gives up. I've fixed the first. Do you want me to fix the second?",
    "The migration ran on the staging database. It added the new column and back-filled about "
    "twelve thousand rows in three seconds. Nothing else changed.",
    "I couldn't reproduce the crash. The tests pass on this branch, and the stack trace you "
    "pasted points at a file that was deleted last week. Can you check which version the report came from?",
    "I've drafted the release notes. They cover the new pairing screen, the faster first load, "
    "and the fix for replies that cut off early. I left the version number blank for you to fill in.",
]

# Said over a reply on the echo page, as someone barging in would.
SAY_OVER = [
    "Stop.",
    "Wait, that's not what I meant.",
    "Hang on, which file was that in?",
    "No, run the tests in backend first.",
    "Actually, cancel that and look at the login page instead.",
    "Okay, and can you also update the readme?",
]

# The turn-detection set. Each is said in the speaker's own words, the way they'd say it to
# Sennet; the page asks for natural pauses. `kind` groups the results.
SCENARIOS = [
    # Fixed words: the whole transcript is the command.
    {"kind": "short", "say": "Stop the reply that's playing.", "example": "stop"},
    {"kind": "short", "say": "Hear the whole of the last reply.", "example": "read me the details"},
    {"kind": "short", "say": "Cancel the task.", "example": "cancel"},
    {"kind": "short", "say": "Approve a request.", "example": "yes / go ahead"},
    {"kind": "short", "say": "Decline a request.", "example": "no"},
    {"kind": "short", "say": "Approve it and remember the answer.", "example": "yes, always"},
    {"kind": "short", "say": "Confirm a destructive action.", "example": "confirm"},
    {"kind": "short", "say": "A one-word follow-up to a question the agent asked.", "example": "the second one"},
    # Simple requests.
    {"kind": "simple", "say": "Ask the agent to run the tests.", "example": "run the tests"},
    {"kind": "simple", "say": "Ask what changed in the last commit.", "example": "what changed in the last commit?"},
    {"kind": "simple", "say": "Ask it to commit what it's done.", "example": "commit that"},
    {"kind": "simple", "say": "Ask why the build failed.", "example": "why did the build fail?"},
    {"kind": "simple", "say": "Ask it to undo its last change.", "example": "undo that last change"},
    # Routed: a project name first, then the request.
    {"kind": "routed", "say": "Name one of your projects, then ask it to run its linter.",
     "example": "in backend, run the linter"},
    {"kind": "routed", "say": "Name a project, then ask for a summary of open TODOs.",
     "example": "API: what TODOs are left?"},
    {"kind": "routed", "say": "Name a project, then ask it to bump a dependency.",
     "example": "in frontend, update react to the latest version"},
    # Several parts in one request.
    {"kind": "multi", "say": "Ask for two separate changes in one request.",
     "example": "rename the config loader, and then update the docs to match"},
    {"kind": "multi", "say": "Ask it to fix a bug, then run the tests, then tell you what it changed.", "example": ""},
    {"kind": "multi", "say": "Ask it to look at three files and compare how they handle errors.", "example": ""},
    {"kind": "multi", "say": "Describe a small feature with a couple of conditions on how it should work.", "example": ""},
    # Working it out while speaking: the pauses that cut people off.
    {"kind": "thinking", "say": "Describe a bug you've seen, working out the details as you go.", "example": ""},
    {"kind": "thinking", "say": "Ask for a refactor you haven't fully decided on yet.", "example": ""},
    {"kind": "thinking", "say": "Start a request, change your mind halfway, and finish it differently.",
     "example": "add a… no, actually, remove the retry flag"},
    {"kind": "thinking", "say": "Explain what you want a new test to check, as if to a colleague.", "example": ""},
    {"kind": "thinking", "say": "Ask a question that needs a number or a name you have to remember mid-sentence.",
     "example": "what was the… the timeout we set for the, um, the websocket?"},
    {"kind": "thinking", "say": "Give feedback on a reply: what was right and what to change.", "example": ""},
    # Answers that trail off or end on a function word.
    {"kind": "trailing", "say": "A request that ends on 'and' before you add the last part.",
     "example": "run the tests and… fix any that fail"},
    {"kind": "trailing", "say": "A request with a list you count off with pauses.",
     "example": "check the router, the cache, and the, um, the session code"},
    {"kind": "trailing", "say": "Start with 'so' or 'okay', pause, then make the request.",
     "example": "okay… so, let's add logging to the upload handler"},
    # Long.
    {"kind": "long", "say": "Describe a task in detail: what, where, and how you'd check it worked. "
                            "Take your time; about twenty to thirty seconds.", "example": ""},
    {"kind": "long", "say": "Explain a design change and why you want it. Take your time.", "example": ""},
]
