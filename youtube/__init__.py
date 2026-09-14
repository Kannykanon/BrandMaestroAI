"""YouTube Automation: approved marketing scripts to published storytelling videos.

An optional module, designed in docs/youtube-automation.md. It depends on the
shared services (database, auth, LLMSingleton, the Brand Brain) and reads
approved scripts from marketing, but nothing in marketing imports from here.
If YouTube Automation is not configured, the rest of the app runs unchanged.
"""
