"use client";
import CvUpload from "@/components/CvUpload";

export default function MyCvPage() {
  return (
    <>
      <h1>Upload my CV</h1>
      <p className="sub">Your CV can only be added to your own record. Your team leader approves it before anything changes.</p>
      <CvUpload member />
    </>
  );
}
