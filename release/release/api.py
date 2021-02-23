import frappe
import functools
import requests
from giturlparse import parse


@frappe.whitelist()
@functools.lru_cache()
def get_branches(git_url):
	url = parse(git_url)
	github_token = frappe.get_single("Release Settings").get_password("github_auth_token")
	res = requests.get(
		f"https://api.github.com/repos/{url.owner}/{url.name}/branches",
		headers={"Authorization": f"token {github_token}"},
	)
	if res.ok:
		return [x["name"] for x in res.json()]
	else:
		res.raise_for_status()
