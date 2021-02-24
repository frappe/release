// Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pull Request", {
	onload(frm) {
		if (!frm.doc.pull_request_description && frm.doc.pull_request_link && frm.doc.docstatus === 0) {
			let pr_data = frm.doc.pull_request_link.split('/')

			let pr_number = pr_data[pr_data.length - 1];
			let pr_repo = pr_data[pr_data.length - 3];
			let pr_org = pr_data[pr_data.length - 4];
			if (frm.doc.pull_request_title.includes("(bp #")) {
				pr_number = frm.doc.pull_request_title.match(/#(\d+)/)[1]
			}
			if (pr_number && pr_repo && pr_org) {
				fetch(`https://api.github.com/repos/${pr_org}/${pr_repo}/pulls/${pr_number}`)
				.then(response => response.json())
				.then(data => {
					frm.set_value('pull_request_description', data.body || 'No description found!');
					frm.save()
				});
			}
		}
	},
	refresh: function(frm) {
		if (frm.doc.pull_request_link) {
			frm.add_custom_button("Open PR", () =>
				window.open(frm.doc.pull_request_link, "_blank")
			);
		}
		setTimeout(function() {
			const preview = frm.get_field("pull_request_description").preview_toggle_btn;
			if (preview.text() === "Preview") {
				preview.click();
			}
		}, 1000);
	},
	status: function(frm) {
		frm.refresh();
	},
});
