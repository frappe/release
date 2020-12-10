// Copyright (c) 2020, Frappe Technologies Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pull Request", {
	refresh: function(frm) {
		if (frm.doc.pull_request_link) {
			frm.add_custom_button("Open PR", () =>
				window.open(frm.doc.pull_request_link, "_blank")
			);
		}
		frappe.realtime.on("release", function (r) {
			console.log(r);
			frm.reload_doc();
		});
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
