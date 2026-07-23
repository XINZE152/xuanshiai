"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    certifications,
    community,
    discovery,
    finance,
    health,
    identity,
    matchmaker,
    meeting,
    membership,
    organization,
    points,
    presence,
    profile,
    regions,
    social,
    users,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(auth.router, tags=["account-auth"])
api_router.include_router(users.router, tags=["account-auth"])
api_router.include_router(certifications.router, tags=["certifications"])
api_router.include_router(membership.router, tags=["membership"])
api_router.include_router(points.router, tags=["points"])
api_router.include_router(regions.router, tags=["regions"])
api_router.include_router(presence.router, tags=["presence"])
api_router.include_router(identity.router, tags=["account-auth"])
api_router.include_router(profile.router, tags=["home-profile"])
api_router.include_router(discovery.router, tags=["home-profile"])
api_router.include_router(discovery.users_router, tags=["home-profile"])
api_router.include_router(matchmaker.router, tags=["matchmaker"])
api_router.include_router(matchmaker.requests_router, tags=["matchmaker"])
api_router.include_router(meeting.router, tags=["matchmaker"])
api_router.include_router(social.router, tags=["social"])
api_router.include_router(community.router, tags=["community"])
api_router.include_router(admin.router, tags=["admin"])
api_router.include_router(matchmaker.admin_router, tags=["admin"])
api_router.include_router(meeting.admin_router, tags=["admin"])
api_router.include_router(finance.admin_router, tags=["admin"])
api_router.include_router(organization.router, tags=["organization"])
api_router.include_router(organization.promotion_router, tags=["organization"])
api_router.include_router(organization.partner_router, tags=["organization"])
api_router.include_router(finance.router, tags=["finance"])


OPENAPI_TAGS = [
    {"name": "account-auth", "description": "Login, account identity, verification, and account security."},
    {"name": "certifications", "description": "Certification submissions and review workflows."},
    {"name": "membership", "description": "Membership information and entitlements."},
    {"name": "points", "description": "Points balances, ledgers, tasks, and redemptions."},
    {"name": "regions", "description": "Province, city, and district lookup data."},
    {"name": "presence", "description": "User online presence and session heartbeats."},
    {"name": "home-profile", "description": "Discovery, recommendations, and profile management."},
    {"name": "matchmaker", "description": "Matchmaker services, meetings, and introductions."},
    {"name": "community", "description": "Community posts, comments, topics, and interactions."},
    {"name": "social", "description": "Introductions, matching, messaging, notifications, and safety."},
    {"name": "admin", "description": "Administrative content, operations, finance, and review tools."},
    {"name": "organization", "description": "Stores, organization members, promotions, and partner teams."},
    {"name": "finance", "description": "Orders, commissions, balances, and withdrawals."},
    {"name": "system", "description": "Health checks and service discovery."},
]
