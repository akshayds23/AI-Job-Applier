import json
import logging
import urllib.request
import re

logger = logging.getLogger("profile_scraper")

class ProfileScraper:
    """Scrapes user's public GitHub, portfolio, and web profile links to extract real skills & projects."""

    async def scrape_github(self, github_url: str) -> dict:
        if not github_url or "github.com" not in github_url:
            return {}

        username = github_url.rstrip("/").split("/")[-1]
        api_url = f"https://api.github.com/users/{username}"
        repos_url = f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10"
        
        headers = {"User-Agent": "Mozilla/5.0"}
        result = {
            "bio": "",
            "skills": set(),
            "projects": []
        }

        try:
            # Fetch User Profile
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                result["bio"] = data.get("bio", "") or ""

            # Fetch Top Repos
            req_repos = urllib.request.Request(repos_url, headers=headers)
            with urllib.request.urlopen(req_repos) as resp_repos:
                repos_data = json.loads(resp_repos.read().decode('utf-8'))
                for repo in repos_data:
                    if repo.get("fork"):
                        continue
                    lang = repo.get("language")
                    if lang:
                        result["skills"].add(lang)
                    
                    name = repo.get("name", "")
                    desc = repo.get("description", "") or ""
                    if name:
                        result["projects"].append({
                            "name": name,
                            "description": desc,
                            "technologies": [lang] if lang else [],
                            "url": repo.get("html_url", "")
                        })

            result["skills"] = list(result["skills"])
            return result
        except Exception as e:
            logger.warning(f"Failed to scrape GitHub profile for {username}: {e}")
            return result

    async def scrape_portfolio(self, portfolio_url: str) -> dict:
        if not portfolio_url or not portfolio_url.startswith("http"):
            return {}
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            req = urllib.request.Request(portfolio_url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                # Extract technologies using simple keyword matching
                techs = ["Python", "FastAPI", "React", "Next.js", "TypeScript", "JavaScript", "Docker", "PostgreSQL", "PyTorch", "LangChain", "Node.js", "Tailwind"]
                found_skills = [t for t in techs if re.search(r'\b' + re.escape(t) + r'\b', html, re.I)]
                return {"skills": found_skills}
        except Exception as e:
            logger.warning(f"Portfolio scrape skipped: {e}")
            return {}
