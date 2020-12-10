// Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on('Release', {
	refresh: function(frm) {
		frm.add_custom_button(
			"Process PRs",
			() => {
				frappe.confirm(`Process Pull Requests raised to ${frm.doc.pre_release_branch}?`,
					function() {
						frm.call("process_pull_requests");
				});
			},
			"Actions"
		);
		frm.add_custom_button(
			"Reset Release Info",
			() => {
				frappe.confirm(`Reset Tag and Name Information of Release ${frm.doc.name}?`,
				function() {
					frm.call("reset_release_info").then(
						frm.reload_doc()
					);
				});
			},
			"Actions"
		);
	}
});
