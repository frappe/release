# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
# For license information, please see license.txt

import datetime
import functools
import json
import os
import re

import frappe
import requests
from frappe.model.document import Document
from giturlparse import parse
from semantic_version import Version

remote = "origin"
ignore_pr_type = ("chore", "bump")
as_md = True  # changes titles, export formats
skip_backports = False
backport_identifiers = ("mergify/bp", "(bp #", "(backport #")

# todo: make git_url, stable and pre release branch set only once -- maybe not...


class Release(Document):
	def autoname(self):
		now = datetime.datetime.now()
		release_title = (
			f"{now.strftime('%B')} {now.strftime('%Y')}: {self.parsed.owner}/{self.parsed.name}"
		)
		self.name = f"{release_title} - {self.stable_branch.replace('-', ' ').title()}"

	def validate(self):
		if self.has_value_changed("git_url"):
			self.validate_git_url()

		if self.has_value_changed("stable_branch") or self.has_value_changed(
			"pre_release_branch"
		):
			self.validate_github_branches()

		if not self.is_new() and not (self.tag_name and self.release_name):
			self.set_release_info()

	def on_update(self):
		self.refresh_doc_on_desk()

	def before_submit(self):
		if not (
			self.passed_manual_testing
			and self.check_post_on_discuss
			and self.check_ready_for_release
		):
			frappe.throw("Can't submit without marking all checks!")

		if not (self.release_name and self.tag_name):
			frappe.throw("Update Release Information before Submitting")

		if not (self.raised_pr_for_release and self.bump_commit_created):
			frappe.throw("Run 'Raise PR for Release' before submitting this release")

		self.status = "Ready"

	def on_submit(self):
		self.create_draft_release()
	@frappe.whitelist()
	def raise_pr_for_release(self):
		self.create_bump_commit_on_pre_release()
		self.raise_pre_release_into_stable()

	def raise_pre_release_into_stable(self):
		if self.raised_pr_for_release:
			return

		data = {
			"title": f"chore: Merge {self.pre_release_branch} into {self.stable_branch}",
			"body": "### TODO\n- [ ] Add release notes",
			"head": self.pre_release_branch,
			"base": self.stable_branch,
			"maintainer_can_modify": True,
		}

		response = requests.post(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/pulls",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
			data=json.dumps(data),
		)
		self._response = response
		if response.ok:
			pr_link = response.json()['html_url']
			frappe.msgprint(f"PR raised: <a href='{pr_link}'>{pr_link}</a>")
		else:
			try:
				message = response.json()["message"]
				error = (
					f': {response.json()["errors"][0]["message"]}'
					if response.json().get("errors")
					else ""
				)
			except:
				response.raise_for_status()
			frappe.throw(f"{message}{error}")

		self.db_set(
			"raised_pr_for_release", True, update_modified=False, notify=True, commit=True,
		)

	def create_bump_commit_on_pre_release(self):
		if self.bump_commit_created:
			return

		from github import InputGitAuthor

		file_path = f"{self.parsed.name}/__init__.py"

		repo = self.GitHub.get_repo(self.parsed.pathname.lstrip("/"))
		file = repo.get_contents(file_path, ref=self.pre_release_branch)
		old_data = file.decoded_content.decode("utf-8")
		data = re.sub("__version__ = .*", f"__version__ = '{self.tag_name}'", old_data)

		author = InputGitAuthor(
			frappe.utils.get_fullname(frappe.session.user), frappe.session.user,
		)
		message = f"chore: Bump to v{self.tag_name}"
		contents = repo.get_contents(file_path, ref=self.pre_release_branch)

		repo.update_file(
			path=contents.path,
			message=message,
			content=data,
			sha=contents.sha,
			branch=self.pre_release_branch,
			author=author,
		)

		self.db_set(
			"bump_commit_created", True, update_modified=False, notify=True, commit=True
		)

	def create_draft_release(self):
		if not self.pending_pull_requests_to_stable:
			frappe.throw(
				"There are open PRs to stable branch. Get them merged before submitting release!"
			)

		if not self.pre_release_merged_into_stable_branch:
			frappe.throw(
				"Check the field `Pre Release Merged Into Stable Branch` before you try"
				" to create a draft release!"
			)

		alert_message = (
			f"#ALERT: Update the branch {self.stable_branch} with a bump commit"
			f" to update its' {self.parsed.name}.__version__ before publishing"
			" this !\n"
			if not self.bump_commit_created
			else ""
		)
		data = json.dumps(
			{
				"tag_name": f"v{self.tag_name}",
				"target_commitish": self.stable_branch,
				"name": self.release_name,
				"body": alert_message
				+ f"# Version {self.tag_name} Release Notes\n# Fixes &"
				f" Enhancements\n# Features\n{self.get_summary()}",
				"draft": True,
			}
		)

		release_request = requests.post(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/releases",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
			data=data,
		)
		if release_request.ok:
			frappe.msgprint(
				"Draft Release Created at {0}".format(
					f"<a href={release_request.json().get('html_url')}>GitHub</a>"
				)
			)
		else:
			response = release_request
			self._response = response

			try:
				message = response.json()["message"]
				error = response.json()["errors"][0]["message"]
			except:
				response.raise_for_status()

			frappe.throw(f"{message}: {error}")

	def before_update_after_submit(self):
		if self.status == "Released":
			now = datetime.datetime.now()
			self.name = f"{self.name} on {now.strftime('%d-%m-%Y')}"

	def validate_git_url(self):
		if not self.parsed.protocol:
			frappe.throw(f"Missing protocal in {self.git_url}")

		if self.parsed.resource != "github.com":
			frappe.throw("Release only supports GitHub at this point", exc=NotImplementedError)

	def validate_github_branches(self):
		auth_token = self.settings.get_password("github_auth_token")
		for branch in [self.stable_branch, self.pre_release_branch]:
			response = requests.head(
				f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/branches/{branch}",
				headers={
					"Authorization": f"token {auth_token}",
					"accept": "application/vnd.github.v3+json",
				},
			)
			if not response.ok:
				frappe.throw(f"Branch {branch} does not exist on {self.git_url}")

	@frappe.whitelist()
	def reset_release_info(self):
		self.set_release_info()
		self.save()

	@frappe.whitelist()
	def process_pull_requests(self):
		self.status = "Processing PRs"
		self.save()
		frappe.enqueue_doc(
			self.doctype, self.name, "_process_pull_requests", queue="long", timeout=1200
		)

	def set_release_info(self):
		self.set_tag_name()
		self.release_name = f"Release {self.tag_name}"

	def set_tag_name(self):
		latest_tag_on_stable = Version(self.get_latest_tag_on_stable().lstrip("v"))
		default_bump_type = {
			"Major": "next_major",
			"Minor": "next_minor",
			"Patch": "next_patch",
		}.get(self.release_type)

		if default_bump_type:
			bump_funct = getattr(latest_tag_on_stable, default_bump_type)
		else:

			def bump_funct():
				from frappe.utils import cint

				_, old_version = latest_tag_on_stable.prerelease
				version = str(cint(old_version) + 1)
				next_beta = f"{str(latest_tag_on_stable).rstrip(old_version)}{version}"
				return next_beta

		self.tag_name = str(bump_funct())

	def get_latest_tag_on_stable(self):
		refs_matches = [
			x for x in self.matching_refs if x["ref"] == f"refs/heads/{self.stable_branch}"
		]

		if refs_matches:
			object_sha = refs_matches[0]["object"]["sha"]
			tags_matches = [x for x in self.tags if x["commit"]["sha"] == object_sha]

			if tags_matches:
				return tags_matches[0]["name"]

		return ""

	def refresh_doc_on_desk(self):
		frappe.publish_realtime("release", "refresh", self.name)

	def _process_pull_requests(self):
		for number, data in self.titles.items():
			try:
				pr = frappe.new_doc("Pull Request")
				pr.pull_request_number = number
				pr.pull_request_title = data["title"]
				pr.pull_request_link = data["link"]
				pr.release = self.name
				pr.insert()
			except frappe.DuplicateEntryError:
				pass
			except Exception:
				frappe.logger("release").info(frappe.get_traceback())

		self.db_set("status", "Pre Release Testing")
		self.refresh_doc_on_desk()

	@property
	def GitHub(self):
		if not getattr(self, "_github_connection", None):
			from github import Github

			self._github_connection = Github(self.settings.get_password("github_auth_token"))

		return self._github_connection

	@property
	@functools.lru_cache()
	def matching_refs(self):
		response = requests.get(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/git/matching-refs/",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
		)
		if response.ok:
			return response.json()

	@property
	@functools.lru_cache()
	def tags(self):
		response = requests.get(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/tags",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
		)
		if response.ok:
			return response.json()

	@property
	def pending_pull_requests_to_stable(self):
		return not requests.get(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/pulls?base={self.stable_branch}",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
		).json()

	@property
	def passed_manual_testing(self):
		return not frappe.get_all(
			"Pull Request", filters={"release": self.name, "status": "Failed"}
		)

	@property
	@functools.lru_cache()
	def settings(self):
		return frappe.get_single("Release Settings")

	@property
	def parsed(self):
		return parse(self.git_url)

	@property
	def commits(self):
		response = requests.get(
			f"https://api.github.com/repos/frappe/frappe/compare/{self.stable_branch}...{self.pre_release_branch}",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
		)
		if not response.ok:
			response.raise_for_status()

		updated_set = set([x["commit"]["message"] for x in response.json()["commits"]])

		if hasattr(self, "_commits") and updated_set != self._commits:
			Release.titles.fget.cache_clear()
		self._commits = updated_set

		return self._commits

	@property
	def pull_requests(self):
		pull_numbers = []
		pr_merge_commits = []

		for commit in self.commits:
			if "#" in commit and not (
				skip_backports and any(txt in commit for txt in backport_identifiers)
			):
				pr_merge_commits.append(commit)

		for commit in pr_merge_commits:
			pull_numbers = pull_numbers + re.findall(r"(?<!\(bp )#(\d+)", commit)

		updated_set = set(pull_numbers)

		if hasattr(self, "_pull_requests") and updated_set != self._pull_requests:
			Release.titles.fget.cache_clear()
		self._pull_requests = updated_set

		return self._pull_requests

	@property
	@functools.lru_cache()
	def titles(self):
		"""Retreives PR titles dict using release.pull_requests from GitHub

		Returns:
			dict: PR number as the key and dict of PR title and GitHub link as the value
		"""
		titles = {}
		organization = self.parsed.owner
		repo_name = self.parsed.name
		authorization_token = self.settings.get_password("github_auth_token")
		total = len(self.pull_requests)

		for i, pull_number in enumerate(self.pull_requests):
			print(f"Fetching information for {i}/{total}", end="\r")

			res = requests.get(
				f"https://api.github.com/repos/{organization}/{repo_name}/pulls/{pull_number}",
				headers={
					"Authorization": f"token {authorization_token}",
					"accept": "application/vnd.github.v3+json",
				},
			)
			if not res.ok:
				continue

			title = res.json().get("title")

			if not title:
				print("Invalid PR {pull_number}: No title found")
				continue

			if title.startswith(ignore_pr_type):
				print(f"Ignoring PR {pull_number}: '{title}'")
				continue

			pr_link = f"https://github.com/{organization}/{repo_name}/pull/{pull_number}"

			titles[pull_number] = {"title": title, "link": pr_link}

		return titles

	def get_summary(self):
		if as_md:
			row_template = "- {y[title]} ([#{x}]({y[link]}))"
		else:
			row_template = "{y[title]}	Open		{y[link]}"

		return "\n".join([row_template.format(x=x, y=y) for x, y in self.titles.items()])

	def export(self):
		ext = ".md" if as_md else ".csv"
		timestamp = datetime.datetime.now().strftime("%d-%m-%Y_%H:%M:%S")

		with open(
			f"diff_{self.parsed.name}_{self.pre_release_branch}_{self.stable_branch}_{timestamp}{ext}",
			"w+",
		) as title_file:
			title_file.write(self.get_summary())
			print("Saved: ", os.path.abspath(title_file.name))
