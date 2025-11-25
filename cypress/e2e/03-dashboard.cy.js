context("Dashboard actions", () => {
	after(() => {
		cy.wait(100);
	});

	it("Club page edit", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/halden-sk");
		cy.contains(".admin-user-div", "test-user");
		cy.get(".admin-user-div").should("have.length", 2);
		cy.get(".remove-admin-btn").eq(1).click();
		cy.contains(
			`You are about to remove user "test-user" from the club's administrator list.`,
		);
		cy.wait(500);
		cy.get("button.confirm").click();
		cy.get(".admin-user-div").should("have.length", 1);
		cy.get(".remove-admin-btn").click();
		cy.contains("You must have at least one administrator listed.");
	});

	it("Contribution page", () => {
		cy.visit(
			"https://halden-sk.routechoices.dev/open-registration-upload-allowed/contribute",
		);
		cy.contains("Enter yourself");
		cy.get("#id_name").type("Thierry Gueorgiou");
		cy.get("#id_short_name").type("🇫🇷 T.Gueorgiou");
		cy.get("#id_device_id-ts-control").type("123456").wait(1000).blur();
		cy.get("button:not([type]),button[type=submit]").eq(0).click();
		cy.contains("Competitor added!");
		cy.get(".upload-route-btn").first().click();

		cy.get("#id_gpx_file").selectFile({
			contents: "cypress/fixtures/Jukola2019/1/gpx/HaldenSK.gpx",
			fileName: "HaldenSK.gpx",
			mimeType: "text/xml",
		});
		cy.get("#uploadRouteModal button:not([type]),button[type=submit]").click();
		cy.contains("Data uploaded!");
	});

	it("Particiption page", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/participations");
		cy.contains(
			"My event with open registration and upload allowed by Halden SK as Aatos (A)",
		);
		cy.get(".edit-info-btn").first().click();
		cy.get("#id_name").clear().type("Kasper Harlem Fosser");
		cy.get("#id_short_name").clear().type("🇳🇴 K.H.Fosser{enter}");
		cy.contains("Info updated!");
		cy.contains(
			"My event with open registration and upload allowed by Halden SK as Kasper Harlem Fosser (🇳🇴 K.H.Fosser)",
		);

		cy.get(".open-upload-btn").first().click();
		cy.get("#id_gpx_file").selectFile(
			"cypress/fixtures/Jukola2019/1/gpx/PR.gpx",
		);
		cy.get(".upload-btn:not(.disabled)").click();
		cy.contains("Data uploaded!");
	});

	it("Registration website", () => {
		cy.visit("https://registration.routechoices.dev");
		cy.get("#name").type("Veijo");
		cy.get("#sname").type("V");
		cy.get("#devid").type("10000000{enter}");
		cy.get("#events").select(1);
		cy.get("#register-btn").click();
		cy.get("#p4").contains("You have been registered.");
	});

	it("Manage devices", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/");
		cy.contains("Halden SK").click();
		cy.contains("Devices").click();
		cy.contains("Add new device").click();
		cy.get("#id_device-ts-control").type("10000000").wait(1000);
		cy.get("#id_nickname").type("MyDevice");
		cy.get("input").contains("Add").click();
		cy.get("#django-messages").contains("Device added successfully");
		cy.contains("MyDevice");
		cy.get(".edit-nick-btn").first().click();
		cy.get("input[placeholder='Nickname'")
			.clear()
			.type("Dev1")
			.wait(500)
			.type("{enter}");
		cy.contains("Dev1");
		cy.contains("MyDevice").should("not.exist");
		cy.get(".remove-btn").first().click().wait(500);
		cy.get("button.confirm").click();
	});

	it("Upgrade account", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/");
		cy.contains("Halden SK").click();
		cy.contains("Upgrade to our paid plan!").click();
		cy.location("pathname").should("eq", "/clubs/halden-sk/upgrade");
		cy.contains("Upgrade my subscription").click();
		cy.contains("Proceed to payment").click();
		cy.origin("https://routechoices.lemonsqueezy.com", () => {
			cy.contains("Test mode is currently enabled.", { timeout: 20_000 });
		});
	});

	it("Import map", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/");
		cy.contains("Halden SK").click();

		for (const gpxFileName of ["trk", "waypoint", "waypoint+trk"]) {
			cy.visit(
				"https://dashboard.routechoices.dev/clubs/halden-sk/maps/upload-gpx",
			);
			cy.get("#id_gpx_file").selectFile(
				`cypress/fixtures/gpx/${gpxFileName}.gpx`,
			);
			cy.get("button:not([type]),button[type=submit]").eq(1).click();
			cy.get("#django-messages").contains(
				"The import of the map was successful!",
			);
		}

		for (const kmzFileName of [
			"Jukola2019/1/map.kmz",
			"maps/multiground.kml",
			"maps/tiled.kmz",
		]) {
			cy.visit(
				"https://dashboard.routechoices.dev/clubs/halden-sk/maps/upload-kmz",
			);
			cy.get("#id_file").selectFile(`cypress/fixtures/${kmzFileName}`);
			cy.get("button:not([type]),button[type=submit]").click();
			cy.get("#django-messages", { timeout: 10_000 }).contains(
				"The import of the map was successful!",
			);
		}
	});

	it("Create map from image", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/");
		cy.contains("Halden SK").click();

		cy.visit("https://dashboard.routechoices.dev/clubs/halden-sk/maps/new");

		cy.get("#id_name").type("Jukola 2019 - 1st Leg (manual calibration)");

		cy.get("#id_image").selectFile("cypress/fixtures/Jukola2019/1/map.jpg");

		cy.get("#calibration-preview-opener").should("not.be.visible");
		cy.get("#calibration-helper-opener").click();
		cy.wait(1000);

		cy.get("#to-calibration-step-2-button-disabled").should("be.visible");
		cy.get("#to-calibration-step-2-button").should("not.be.visible");
		cy.get("#raster-map").click(70, 10);
		cy.get("#world-map").click(70, 10);
		cy.get("#raster-map").click(200, 10);
		cy.get("#world-map").click(200, 10);
		cy.get("#raster-map").click(200, 200);
		cy.get("#to-calibration-step-2-button-disabled").should("be.visible");
		cy.get("#to-calibration-step-2-button").should("not.be.visible");
		cy.get("#world-map").click(200, 200);
		cy.get("#to-calibration-step-2-button-disabled").should("not.be.visible");
		cy.get("#to-calibration-step-2-button").should("be.visible");
		cy.get("#raster-map").click(10, 200);
		cy.get("#world-map").click(10, 200);

		cy.get("#to-calibration-step-2-button").click();

		cy.get("#validate-calibration-button").click();

		cy.get("#calibration-preview-opener").should("be.visible");
		cy.get("#id_calibration_string")
			.invoke("val")
			.then((val) => {
				expect(/^[-]?\d+(\.\d+)?(,[-]?\d+(\.\d+)?){7}$/.test(val));
			});
		cy.get("button:not([type]),button[type=submit]").click();

		cy.get("#django-messages").contains("Map created successfully");
	});

	it("Create a club", () => {
		cy.login();

		// Create club
		cy.createClub();
		cy.contains("Kangasala SK");
		cy.location("pathname").should("eq", "/clubs/kangasala-sk/");

		// modify club
		cy.get("#id_website").type("https://www.kangasalask.fi");
		cy.get("#id_description")
			.clear()
			.type("## Kangasala SK  \n## GPS Tracking");

		cy.get("#id_logo").selectFile("cypress/fixtures/KSK_logo.png");
		cy.get("#id_banner").selectFile("cypress/fixtures/KSK_banner.jpg");

		cy.get("button:not([type]),button[type=submit]").click();
		cy.contains("Changes saved successfully", { timeout: 10_000 });
	});

	it("Create events", () => {
		cy.login();
		cy.visit("https://dashboard.routechoices.dev/clubs/");
		cy.contains("Halden SK").click();

		// Create Map
		cy.createMap();

		// Create Event with minimal info
		cy.visit("https://dashboard.routechoices.dev/clubs/halden-sk/events/");
		cy.get("a").contains("Create new event").click();
		cy.location("pathname").should("eq", "/clubs/halden-sk/events/new");

		cy.get("#id_name").type("Jukola 2019 - 1st Leg");
		cy.get("#id_event_set-ts-control").parent().click().wait(300);
		cy.get("#id_event_set-ts-control").type("{backspace}Jukola 2019").wait(300);
		cy.get(".ts-dropdown-content > .create").click();
		cy.get("#id_start_date").focus().clear().type("2019-06-15T23:00:00");
		cy.get("#id_end_date").focus().clear().type("2019-06-16T12:00:00");
		cy.get("#id_map").select("Jukola 2019 - 1st Leg");
		cy.get("button:not([type]),button[type=submit]").first().click();

		// Edit event we just created
		cy.location("pathname").should("eq", "/clubs/halden-sk/events/");
		cy.get("a").contains("Jukola 2019 - 1st Leg").click();

		cy.get("#csv_input").selectFile("cypress/fixtures/startlist.csv");
		cy.get("#id_competitors-2-name").should("have.value", "Samuel Heinonen");
		cy.get("button[name='save_continue']").click();
		const runners = [
			{
				club: "KooVee",
				name: "Tim Robertson",
			},
			{
				club: "PR",
				name: "Samuel Heinonen",
			},
			{
				club: "HaldenSK",
				name: "Niels Christian Hellerud",
			},
		];
		for (const runner of runners) {
			cy.get("#upload_route_btn").click();
			cy.get("#id_competitor").select(runner.name);
			cy.get("#id_gpx_file").selectFile(
				`cypress/fixtures/Jukola2019/1/gpx/${runner.club}.gpx`,
			);
			cy.get("button:not([type]),button[type=submit]").click();
			cy.contains("The upload of the GPX file was successful!");
		}

		// Test the event view
		// TODO: move to own test
		cy.visit("https://halden-sk.routechoices.dev/Jukola-2019-1st-leg");
		cy.origin("https://halden-sk.routechoices.dev", () => {
			cy.contains("Niels Christian Hellerud", { timeout: 20_000 }); // in competitor list

			//// toggle competitor
			cy.get("#toggleAllSwitch").uncheck();

			cy.get(".competitor-switch").eq(2).check();
			cy.contains("#map", "🇫🇮 KooVee");
			cy.get(".competitor-switch").eq(2).uncheck();
			cy.contains("#map", "🇫🇮 KooVee").should("not.exist");
			cy.get(".competitor-switch").eq(1).check();
			cy.contains("#map", "🇫🇮 Paimion Rasti");

			cy.get("#toggleAllSwitch").check();

			//// change runner color
			cy.get(".color-tag").eq(1).click();
			cy.contains("Select new color for Samuel Heinonen");
			cy.get(".IroWheel").first().should("be.visible").click(50, 50);
			cy.get("#save-color").click();

			//// center on runner
			cy.get('[aria-label="Center"]').eq(1).click();
			cy.wait(200);

			//// move progress bar and focus on runner
			cy.get("#full_progress_bar").click(50, 7);
			cy.get(".competitor-focus-btn").eq(1).click();
			cy.wait(500);

			//// toogle full route
			cy.get(".competitor-highlight-btn").eq(1).click();
			cy.get(".competitor-full-route-btn").eq(1).click();
			cy.wait(500);
			cy.get(".competitor-highlight-btn").eq(1).click();
			cy.get(".competitor-full-route-btn").eq(1).click();

			//// random location mass start
			cy.get("#real_time_button").should("have.class", "active");
			cy.get("#map").dblclick(70, 100);
			cy.wait(1000);
			cy.get("#real_time_button").should("not.have.class", "active");

			//// Show grouping
			cy.get("#options_show_button").click();
			cy.get("#toggleClusterSwitch").click();
			cy.get(".leaflet-control-grouping").first().contains("Group A");
			cy.contains("#map", "Group A");
			cy.contains("#map", "🇫🇮 Paimion Rasti");
			cy.contains("#map", "🇫🇮 KooVee").should("not.exist");
			cy.get("#toggleClusterSwitch").click();

			//// mass start simulation
			cy.get("#mass_start_button").click();
			cy.wait(1000);
		});

		// Create second event with all fields info
		cy.createMap("Another map");
		cy.intercept("POST", "/clubs/halden-sk/events/new").as("eventSubmit");
		cy.visit("https://dashboard.routechoices.dev/clubs/halden-sk/events/new");
		cy.get("#id_name").type("Jukola 2019 - 2nd Leg");
		cy.get("#id_event_set-ts-control").parent().click().wait(300);
		cy.get("#id_event_set-ts-dropdown > .option").eq(1).click().wait(300);
		cy.get("#id_start_date").focus().clear().type("2019-06-16T00:00:00");
		cy.get("#id_end_date").focus().clear().type("2019-06-16T03:00:00");
		cy.get("#id_geojson_layer").selectFile(
			"cypress/fixtures/geojson/valid.geojson",
		);
		cy.get("#id_map").select("Jukola 2019 - 1st Leg"); // doesnt matter
		cy.get("#id_map_assignations-0-map").select("Another map");
		cy.get("#id_map_assignations-0-title").type("Another map");
		cy.get("#id_competitors-0-device-ts-control").type("10000000").wait(1000);
		cy.get("#id_competitors-0-name").type("Björn Ekeberg");
		cy.get("#id_competitors-0-short_name").type("🇳🇴 IL Tyrving");
		cy.get("#id_competitors-0-start_time")
			.focus()
			.clear()
			.type("2019-06-16T00:00:10");
		cy.get("button:not([type]),button[type=submit]").first().click();

		cy.wait("@eventSubmit").then(({ request, response }) => {
			expect(response.statusCode).to.eq(302);
			expect(request.body).to.contain(
				'form-data; name="competitors-0-device"\r\n\r\n2\r\n',
			);
		});
		cy.location("pathname").should("eq", "/clubs/halden-sk/events/");

		// test the event view
		cy.visit("https://halden-sk.routechoices.dev/Jukola-2019-2nd-leg");
		cy.origin("https://halden-sk.routechoices.dev", () => {
			cy.contains("Björn Ekeberg", { timeout: 20_000 });
			cy.contains("Another map", { timeout: 20_000 });
		});
	});
});
