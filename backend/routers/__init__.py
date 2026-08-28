from backend.routers import agent, analytics, auth_users, contributions, integrations, projects, system, tasks

ALL_ROUTERS = (system.router, auth_users.router, projects.router, tasks.router, contributions.router, analytics.router, agent.router, integrations.router)
