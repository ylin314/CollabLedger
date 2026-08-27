from backend.routers import agent, analytics, auth_users, classrooms, contributions, integrations, projects, system, tasks

ALL_ROUTERS = (system.router, auth_users.router, classrooms.router, projects.router, tasks.router, contributions.router, analytics.router, agent.router, integrations.router)