frappe.listview_settings["Release"] = {
    onload: function(listview) {
        listview.page.add_menu_item(__("Release Settings"), function() {
            frappe.set_route("Form", "Release Settings", "Release Settings");
        });
    }
}