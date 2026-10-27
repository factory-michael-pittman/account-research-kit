import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import catalog as catalog_mod, fit, geo

_, SETTINGS = catalog_mod.load()


def record(**kw):
    base = {"Id": "001x", "Name": "Acme Corp", "Website": "acme.com", "Type": "Prospect",
            "BillingCountry": "United States", "BillingState": "California",
            "ZI_Employee_Count__c": "500", "ZI_Engineering_Headcount__c": "80",
            "ZI_Primary_Industry__c": "Software", "ZI_Primary_Sub_Industry__c": "",
            "ZI_Past_1_Year_Employee_Growth_Rate__c": "", "Account_Tier__c": "",
            "ZI_Ultimate_Parent_Company_Name__c": ""}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


class Geo(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(geo.normalize_country("United States"), "US")
        self.assertEqual(geo.normalize_country("Canada"), "CA")
        self.assertEqual(geo.normalize_country("Germany"), "Germany")
        self.assertEqual(geo.normalize_region("US", "CA"), "CA")
        self.assertEqual(geo.normalize_region("US", "california"), "CA")
        self.assertEqual(geo.normalize_region("CA", "British Columbia"), "BC")
        self.assertEqual(geo.resolve_location(record(BillingState="")), ("US", "", "unresolved"))

    def test_region_allowed(self):
        self.assertEqual(geo.country_outside(SETTINGS.countries, "US"), "")
        self.assertEqual(geo.region_outside(SETTINGS.regions, "US", "CA"), "")
        self.assertIn("not in the allowed list",
                      geo.region_outside(SETTINGS.regions, "US", "NY"))
        self.assertIn("country", geo.country_outside(SETTINGS.countries, "GB"))
        # unknown locations are never exclusions: they are enrichment lookups
        self.assertEqual(geo.country_outside(SETTINGS.countries, ""), "")
        self.assertEqual(geo.region_outside(SETTINGS.regions, "US", ""), "")
        # empty allowlists mean "any"
        self.assertEqual(geo.country_outside(frozenset(), "GB"), "")
        self.assertEqual(geo.region_outside(frozenset(), "US", "NY"), "")


class Filters(unittest.TestCase):
    def assess(self, **kw):
        return fit.assess(SETTINGS, record(**kw), {"__default__": 0.2})

    def test_passes_a_western_software_account(self):
        a = self.assess()
        self.assertEqual(a.status, "pass")
        self.assertEqual((a.country, a.region), ("US", "CA"))
        self.assertEqual(a.eng, 80)

    def test_excludes_east_of_the_territory(self):
        self.assertEqual(self.assess(BillingState="New York").status, "excluded")

    def test_excludes_small_engineering(self):
        a = self.assess(ZI_Engineering_Headcount__c="20")
        self.assertEqual(a.status, "excluded")
        self.assertIn("engineering headcount", a.reason)

    def test_excludes_competitor_and_junk(self):
        self.assertEqual(self.assess(Website="cursor.com").status, "excluded")
        self.assertEqual(self.assess(Name="TBD").status, "excluded")

    def test_excludes_public_sector_and_parents(self):
        self.assertEqual(self.assess(ZI_Primary_Industry__c="Government").status, "excluded")
        a = self.assess(ZI_Ultimate_Parent_Company_Name__c="Giant Holding Ltd")
        self.assertEqual(a.status, "excluded")
        self.assertIn("Giant Holding", a.reason)
        # a parent whose name shares words with the account is fine
        self.assertEqual(self.assess(ZI_Ultimate_Parent_Company_Name__c="Acme").status, "pass")

    def test_customers_are_expansion(self):
        self.assertEqual(self.assess(Type="Customer").status, "expansion")

    def test_unresolved_without_location_or_size(self):
        self.assertEqual(self.assess(BillingState="", ShippingState="").status, "unresolved")
        self.assertEqual(self.assess(ZI_Engineering_Headcount__c="",
                                     ZI_Employee_Count__c="").status, "unresolved")

    def test_estimated_headcount_from_industry_share(self):
        shares = fit.engineering_share_by_industry(
            [record(ZI_Engineering_Headcount__c="200", ZI_Employee_Count__c="1000")])
        eng, estimated = fit.engineering_headcount(
            record(ZI_Engineering_Headcount__c="", ZI_Employee_Count__c="500"), shares)
        self.assertEqual(eng, 100)  # 500 x the measured 0.2 share
        self.assertTrue(estimated)


class FitScore(unittest.TestCase):
    def score(self, **kw):
        return fit.fit_score(SETTINGS, record(**kw), 80, False)

    def test_components(self):
        score, parts = self.score()
        self.assertEqual(score, 3)  # eng band +1, software-heavy +2
        self.assertIn("eng band +1", parts)
        self.assertIn("software-heavy +2", parts)

    def test_it_services_penalized(self):
        score, parts = self.score(ZI_Primary_Industry__c="Business Services",
                                  ZI_Primary_Sub_Industry__c="Custom Software & IT Services")
        self.assertIn("IT services -2", parts)

    def test_growth_and_stack(self):
        score, parts = self.score(ZI_Past_1_Year_Employee_Growth_Rate__c="25",
                                  ZI_Product_Technology_Products_Used__c="Java, Kubernetes")
        self.assertIn("growth >10% +1", parts)
        self.assertIn("stack match +1", parts)

    def test_estimation_penalty(self):
        score, parts = fit.fit_score(SETTINGS, record(), 80, True)
        self.assertIn("estimated headcount -1", parts)


if __name__ == "__main__":
    unittest.main()
