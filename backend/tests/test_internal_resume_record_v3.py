import json
import tempfile
import unittest
from pathlib import Path

from extractor.internal_resume_record_v3 import (
    InternalResumeRecordError,
    load_internal_resume_record,
)
from extractor.v3_data_split_registry import V3DataSplitRegistry


class InternalResumeRecordV3Tests(unittest.TestCase):
    def _write(self, payload):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "candidate_9999.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.addCleanup(tmp.cleanup)
        return path

    def test_valid_internal_record_loads(self):
        text = "教育经历\n某大学\n项目经历\n使用Python完成数据分析并输出结果。"
        path = self._write(
            {
                "schema_version": "real_resume_dataset_v1",
                "candidate_id": "candidate_9999",
                "source_category": "人工智能",
                "source_file": "source_9999.json",
                "resume_text": text,
                "sections": {"education": ["教育经历\n某大学"], "project_experience": ["项目经历\n使用Python完成数据分析并输出结果。"]},
                "metadata": {"text_length": len(text)},
            }
        )
        record = load_internal_resume_record(path)
        self.assertEqual(record.candidate_id, "candidate_9999")
        self.assertEqual(record.source_category, "人工智能")
        self.assertEqual(record.resume_text, text)
        self.assertEqual(len(record.file_sha256), 64)

    def test_raw_platform_snapshot_is_rejected(self):
        path = self._write(
            {
                "schema_version": "1.0",
                "candidate_id": "candidate_9999",
                "source_category": "人工智能",
                "resume_text": "x",
                "sections": {},
                "raw_visible_text": "PII",
            }
        )
        with self.assertRaises(InternalResumeRecordError):
            load_internal_resume_record(path)

    def test_metadata_length_mismatch_is_rejected(self):
        path = self._write(
            {
                "schema_version": "real_resume_dataset_v1",
                "candidate_id": "candidate_9999",
                "source_category": "人工智能",
                "resume_text": "abcdef",
                "sections": {},
                "metadata": {"text_length": 5},
            }
        )
        with self.assertRaises(InternalResumeRecordError):
            load_internal_resume_record(path)


class V3DataSplitRegistryTests(unittest.TestCase):
    def test_split_counts_and_gap_are_locked(self):
        registry = V3DataSplitRegistry.load()
        self.assertEqual(len(registry.pilot_ids), 25)
        self.assertEqual(len(registry.legacy_blind_ids), 40)
        self.assertEqual(len(registry.exposed_ids), 65)
        self.assertEqual(len(registry.holdout_ids), 55)
        self.assertEqual(registry.holdout_category_counts["人工智能"], 25)
        self.assertEqual(registry.holdout_category_counts["智能科学"], 15)
        self.assertEqual(registry.holdout_category_counts["计算机科学与技术"], 15)
        self.assertEqual(
            set(registry.final_blind_missing_categories), {"大数据", "智能系统"}
        )

    def test_recommended_smoke_records_are_development_only(self):
        registry = V3DataSplitRegistry.load()
        self.assertEqual(
            registry.smoke_ids,
            ("candidate_0027", "candidate_0050", "candidate_0104"),
        )
        self.assertTrue(all(cid in registry.legacy_blind_ids for cid in registry.smoke_ids))
        self.assertTrue(all(cid not in registry.holdout_ids for cid in registry.smoke_ids))


if __name__ == "__main__":
    unittest.main()
