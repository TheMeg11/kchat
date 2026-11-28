// Extracted editable application component; vendor code stays in the recovered entry.
export function createPasswordChangeForm({b,u,ln,Te,Ke,Ae}) {
  return function PasswordChangeForm() {
  const [current,setCurrent]=b.useState("");
  const [next,setNext]=b.useState("");
  const [confirmation,setConfirmation]=b.useState("");
  const [busy,setBusy]=b.useState(false);
  const [error,setError]=b.useState("");
  const notice=ln();
  async function submit(event) {
    event.preventDefault();
    if(busy)return;
    if(next.length<10 || next !== confirmation){setError("رمز جدید باید حداقل ۱۰ کاراکتر باشد و با تکرار آن مطابقت داشته باشد.");return;}
    setBusy(true);setError("");
    try {
      await Te("/auth/change-password",{method:"POST",body:{currentPassword:current,newPassword:next}});
      setCurrent("");setNext("");setConfirmation("");
      notice.success("رمز تغییر کرد؛ نشست‌های قبلی باطل شدند. همین دستگاه متصل می‌ماند.");
    } catch(error){setError(error.message);} finally {setBusy(false);}
  }
  return u.jsxs("form",{onSubmit:submit,className:"kanar-card rounded-2xl p-5 space-y-3",children:[
    u.jsx("h2",{className:"text-sm font-bold",children:"تغییر رمز عبور (بدون احراز دومرحله‌ای)"}),
    u.jsx(Ke,{label:"رمز فعلی",type:"password",autoComplete:"current-password",value:current,onChange:e=>setCurrent(e.target.value),required:true}),
    u.jsx(Ke,{label:"رمز جدید",type:"password",autoComplete:"new-password",minLength:10,maxLength:512,value:next,onChange:e=>setNext(e.target.value),required:true}),
    u.jsx(Ke,{label:"تکرار رمز جدید",type:"password",autoComplete:"new-password",value:confirmation,onChange:e=>setConfirmation(e.target.value),required:true}),
    error && u.jsx("p",{role:"alert",children:error}),
    u.jsx(Ae,{type:"submit",loading:busy,disabled:busy,children:"ذخیره رمز جدید"})
  ]});
};
}
