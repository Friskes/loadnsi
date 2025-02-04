NSI_FIXTURES_FOLDER = 'fixtures/nsi'
NSI_PASSPORTS = {
    'file': 'dictionary_nsi.json',
    'model': 'nsi.DictionaryNsi',
    # 'include': ('oid', 'version', 'fullName'),
    # 'exclude': ('fullName', 'shortName'),
}
DICT_INTERNAL_PK = 'custom_id'
PASSPORTS_REL = 'nsi_dictionary'
PARENT_DICT_CLS = 'BaseNsi'
NSI_DICTIONARIES = {
    'organization_nsi.json': {
        'model': 'nsi.OrganizationNsi',
        'oid': '1.2.643.5.1.13.13.11.1461',
        # 'filter': lambda r: r.get('regionName') == 'Астраханская область',
        'create_sql': True,
    },
    'department_nsi.json': {
        'model': 'nsi.DepartmentNsi',
        'oid': '1.2.643.5.1.13.13.99.2.114',
        'create_sql': True,
    },
    'position_nsi.json': {
        'model': 'nsi.PositionNsi',
        'oid': '1.2.643.5.1.13.13.99.2.181',
    },
    'ensurance.json': {
        'model': 'nsi.EnsuranceNsi',
        'oid': '1.2.643.5.1.13.13.99.2.183',
    },
    'placement.json': {
        'model': 'nsi.PlacementNsi',
        'oid': '1.2.643.5.1.13.13.99.2.322',
    },
    'payment.json': {
        'model': 'nsi.PaymentNsi',
        'oid': '1.2.643.5.1.13.13.11.1039',
    },
    'subjects_rf.json': {
        'model': 'nsi.SubjectRF',
        'oid': '1.2.643.5.1.13.13.99.2.206',
    },
    'document_types_nsi.json': {
        'model': 'nsi.DocumentTypesNsi',
        'oid': '1.2.643.5.1.13.13.11.1522',
    },
    'medical_card_types_nsi.json': {
        'model': 'nsi.MedicalCardTypesNsi',
        'oid': '1.2.643.5.1.13.13.99.2.723',
    },
    'nosological_diagnosis_types_nsi.json': {
        'model': 'nsi.NosologicalDiagnosisTypesNsi',
        'oid': '1.2.643.5.1.13.13.11.1077',
    },
    'family_status_nsi.json': {
        'model': 'nsi.FamilyStatusNsi',
        'oid': '1.2.643.5.1.13.13.99.2.15',
    },
    'education_nsi.json': {
        'model': 'nsi.EducationNsi',
        'oid': '1.2.643.5.1.13.13.99.2.16',
    },
    'employment_nsi.json': {
        'model': 'nsi.EmploymentNsi',
        'oid': '1.2.643.5.1.13.13.11.1038',
    },
    'identity_doc_nsi.json': {
        'model': 'nsi.IdentityDocNsi',
        'oid': '1.2.643.5.1.13.13.99.2.48',
    },
    'family_relationship.json': {
        'model': 'nsi.FamilyRelationship',
        'oid': '1.2.643.5.1.13.13.99.2.14',
    },
    'legal_guardian_doc_dictionary.json': {
        'model': 'nsi.LegalGuardianDocNsi',
        'oid': '1.2.643.5.1.13.13.99.2.313',
        # 'filter': lambda r: 'Свидетельство' in r.get('name', ''),
        # 'include': ('ID', 'REPRESENTED', 'ATTRIBUTE_FACE'),
        # 'exclude': ('REPRESENTED', 'ATTRIBUTE_FACE'),
    },
    'address_type_nsi.json': {
        'model': 'nsi.AddressTypeNsi',
        'oid': '1.2.643.5.1.13.13.11.1504',
    },
    'oms_police_type_nsi.json': {
        'model': 'nsi.OmsPoliceTypeNsi',
        'oid': '1.2.643.5.1.13.13.11.1035',
    },
}
