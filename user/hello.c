#include "kernel/types.h"
#include "user/user.h"

int
main(void)
{
  printf("sum = %d\n", hello(1, 2, 3, 4));
  exit(0);
}
