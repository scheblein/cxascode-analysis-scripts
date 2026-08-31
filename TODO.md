# TODO

- Compare what is exported to what is included in the include/exclude filter and display the delta. Example filter:

  `Retrieving exporters filtered list &[genesyscloud_architect_datatable::^CIB_ genesyscloud_architect_ivr::^CIB_ genesyscloud_architect_schedulegroups::^CIB_ genesyscloud_architect_schedules::^CIB_ genesyscloud_architect_user_prompt::^CIB_ genesyscloud_flow::^[A-Z]+_CIB_ genesyscloud_integration_action::^CIB_ genesyscloud_outbound_campaign::^CIB_ genesyscloud_quality_forms_evaluation::^CIB_ genesyscloud_recording_media_retention_policy::^CIB_ genesyscloud_responsemanagement_library::^(?:[A-Za-z]+_CIB_|CIB_) genesyscloud_responsemanagement_response::^(?:[A-Za-z]+_CIB_|CIB_) genesyscloud_routing_queue::^CIB_ genesyscloud_routing_queue_outbound_email_address::^CIB_ genesyscloud_routing_skill::^CIB_ genesyscloud_routing_wrapupcode::^CIB_ genesyscloud_script::^CIB_]`

  Likely needs to happen in the normalization step — add an attribute to each record indicating whether the resource is in the filter.

- Add a count of dependencies — i.e. how many times something was exported.

- Display 429 output — how much time was spent waiting.
