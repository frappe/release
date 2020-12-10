# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from giturlparse import parse


class PullRequest(Document):
	def before_insert(self):
		existing_pull_request = frappe.db.exists(
			self.doctype, {"pull_request_link": self.pull_request_link, "docstatus": ("!=", 2)}
		)
		if existing_pull_request:
			frappe.throw(
				f"Another Pull Request {existing_pull_request} already exists!",
				exc=frappe.DuplicateEntryError,
			)

		if (
			not self.pull_request_description and self.pull_request_link and self.docstatus == 0
		):
			self.update_missing_description()

	def before_submit(self):
		if self.status != "Passed":
			frappe.throw("Can't submit Pull Request which hasn't passed manual testing")

	def on_submit(self):
		if not frappe.db.exists("Pull Request", {"release": self.release, "docstatus": 0}):
			frappe.db.set_value("Release", self.release, "status", "Ready")

	def _setup_pull_request_info(self):
		pr_url = parse(self.pull_request_link)
		repo_url = parse(pr_url.href.rstrip(pr_url.pathname))

		self._pr_number = pr_url.name
		self._repo = repo_url.name
		self._org = repo_url.owner

	def update_missing_description(self):
		self._setup_pull_request_info()

		if self._pr_number and self._repo and self._org:
			self.pull_request_description = self.retrieve_pull_request_body()

	def retrieve_pull_request_body(self):
		import requests

		self._setup_pull_request_info()
		res = requests.get(
			f"https://api.github.com/repos/{self._org}/{self._repo}/pulls/{self._pr_number}"
		)

		if res.ok:
			return res.json().get("body")
