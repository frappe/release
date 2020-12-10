# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import git
import os
from giturlparse import parse
import datetime
import re
import requests
import functools
import datetime

from semantic_version import Version

remote = "origin"
ignore_pr_type = ("chore", "bump")
as_md = True  # changes titles, export formats
skip_backports = False
backport_identifiers = ("mergify/bp", "(bp #")


class Release(Document):
	def autoname(self):
		now = datetime.datetime.now()
		self.name = (
			f"{now.strftime('%B')} {now.strftime('%Y')}: {self.parsed.owner}/{self.parsed.name}"
		)

	def validate(self):
		if not self.parsed.protocol:
			frappe.throw(f"Missing protocal in {self.git_url}")
		if not (self.tag_name and self.release_name):
			self.set_release_info()

	def after_insert(self):
		self.clone_repo()
		self.fetch_remotes()

	def on_update(self):
		self.refresh_doc_on_desk()

	def before_submit(self):
		if not (
			self.pending_pull_requests_to_stable
			and self.passed_manual_testing
			and self.check_post_on_discuss
			and self.check_ready_for_release
		):
			frappe.throw("Can't submit without marking all checks!")

		if not (self.release_name and self.tag_name):
			frappe.throw("Update Release Information before Submitting")
		self.status = "Ready"

	def on_submit(self):
		# create a github draft release!!!
		release_request = requests.post(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/releases",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}",
				"accept": "application/vnd.github.v3+json",
			},
			data={
				"tag_name": self.tag_name,
				"name": self.release_name,
				"body": (
					f"#ALERT: Update the branch {self.stable_branch} with a bump commit"
					f" to update its' {self.parsed.name}.__version__ before publishing"
					f" this !\n# Version {self.tag_name} Release Notes\n# Fixes &"
					f" Enhancements\n# Features\n{self.get_summary()}"
				),
				"draft": True,
			},
		)
		if release_request.ok:
			frappe.msgprint(
				"Draft Release Created at {0}".format(
					f"<a href={release_request.json().get('html_url')}>GitHub</a>"
				)
			)
		else:
			release_request.raise_for_status()

	def before_update_after_submit(self):
		if self.status == "Released":
			now = datetime.datetime.now()
			self.name = f"{self.name} on {now.strftime('%d-%m-%Y')}"

	def on_trash(self):
		if os.path.exists(self.local_clone):
			import shutil

			shutil.rmtree(self.local_clone)

	def reset_release_info(self):
		self.set_release_info()
		self.save()

	def process_pull_requests(self):
		self.status = "Processing PRs"
		self.save()

		frappe.enqueue_doc(
			self.doctype, self.name, "_process_pull_requests", queue="long", timeout=1200
		)

	def set_release_info(self):
		self.fetch_remotes()
		self.repo.checkout(self.stable_branch)
		self.repo.pull()
		self.set_tag_name()
		self.release_name = f"Release {self.tag_name}"

	def set_tag_name(self):
		latest_tag_on_stable = Version(self.repo.describe(tags=True, abbrev=0).lstrip("v"))
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

		self.db_set("status", "Pre Release Testing")
		self.refresh_doc_on_desk()

	@property
	def pending_pull_requests_to_stable(self):
		return not requests.get(
			f"https://api.github.com/repos/{self.parsed.owner}/{self.parsed.name}/pulls?base={self.stable_branch}",
			headers={
				"Authorization": f"token {self.settings.get_password('github_auth_token')}"
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
	def local_clone(self):
		return os.path.join(self.settings.clones_directory, self.parsed.name)

	def clone_repo(self):
		git.Repo.clone_from(self.git_url, self.local_clone)

	@property
	def repo(self):
		return git.Git(self.local_clone)

	def fetch_remotes(self):
		self.repo.fetch(remote, self.stable_branch)
		self.repo.fetch(remote, self.pre_release_branch)
		Release.titles.fget.cache_clear()

	@property
	def commits(self):
		updated_set = set(
			self.repo.log(
				f"{remote}/{self.stable_branch}..{remote}/{self.pre_release_branch}",
				r"--pretty=format:%s",
				"--abbrev-commit",
			).split("\n")
		)

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
				headers={"Authorization": f"token {authorization_token}"},
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
